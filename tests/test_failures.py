import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import cast
from urllib.parse import quote_plus

from anthropic import APIConnectionError, AsyncAnthropic
import httpx2
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS, REQUEST_TIMEOUT

from agent import agent_loop
from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response


def query(call_id, name="maps_text_search", **arguments):
    return {"type": "tool_use", "id": call_id, "name": name,
            "input": arguments or {"keywords": "西湖", "city": "杭州"}}


def map_result(data=None, *, text="", is_error=False):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        structured_content=data, is_error=is_error,
    )


class FaultMapService(MapService):
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        result = next(self.outcomes)
        if isinstance(result, Exception):
            raise result
        return result


class FailureHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_service_failure_keeps_success_and_allows_followup(self):
        class FailingModel(ModelService):
            async def create(self, **kwargs):
                if self.requests:
                    raise APIConnectionError(request=httpx2.Request("POST", "https://model.example.test"))
                return await super().create(**kwargs)

        service = FaultMapService([map_result({"name": "西湖", "address": "杭州市西湖区"})])
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = FailingModel([response([query("ok")], "tool_use")])
        history = [{"role": "user", "content": "查询杭州西湖"}]

        answer = await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

        self.assertIn("模型", answer)
        self.assertNotIn("杭州市西湖区", answer)
        self.assertIn("待核实", answer)
        history.append({"role": "user", "content": "服务恢复了，继续"})
        followup = ModelService([response([{"type": "text", "text": "西湖位于杭州市西湖区。"}])])
        await agent_loop(history, cast(AsyncAnthropic, followup), tools, "test-model")
        results = followup.requests[0]["messages"][2]["content"]
        self.assertEqual(results[0]["tool_use_id"], "ok")
        self.assertFalse(results[0]["is_error"])

    async def test_auth_quota_and_connection_stop_remaining_calls_and_retries(self):
        request = httpx2.Request("POST", "https://mcp.example.test/mcp?key=secret")
        faults = [
            (map_result({"status": "0", "infocode": "10001", "info": "INVALID_USER_KEY"}), "Key"),
            (map_result(text='{"status":"0","infocode":"40000","info":"QUOTA_PLAN_RUN_OUT"}'), "额度"),
            (httpx2.HTTPStatusError("拒绝", request=request,
                                    response=httpx2.Response(401, request=request)), "Key"),
            (httpx2.HTTPStatusError("限流", request=request,
                                    response=httpx2.Response(429, request=request)), "额度"),
            (httpx2.ConnectError("无法连接", request=request), "连接"),
            (MCPError(CONNECTION_CLOSED, "Connection closed"), "连接"),
            (ExceptionGroup("transport failed", [httpx2.ConnectError("断连", request=request)]), "连接"),
            (map_result(text="INVALID_USER_KEY", is_error=True), "Key"),
            (map_result(text="DAILY_QUERY_OVER_LIMIT", is_error=True), "额度"),
            (map_result(text="API 调用失败：CUQPS_HAS_EXCEEDED_THE_LIMIT", is_error=True), "额度"),
        ]
        for fault, explanation in faults:
            with self.subTest(explanation=explanation, fault=type(fault).__name__):
                service = FaultMapService([
                    map_result({"name": "西湖", "address": "杭州市西湖区"}), fault,
                ])
                tools = AmapTools(cast(Client, service))
                await tools.discover()
                model = ModelService([response([
                    query("ok"), query("fault"), query("unexecuted")
                ], "tool_use")])
                history = [{"role": "user", "content": "查询杭州的地点"}]

                answer = await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

                self.assertEqual(len(service.calls), 2)
                self.assertEqual(len(model.requests), 1)
                self.assertIn("地图查询已暂停", answer)
                self.assertNotIn("杭州市西湖区", answer)
                self.assertIn("杭州市西湖区", str(history))
                self.assertIn("待核实", answer)
                results = history[-2]["content"]
                self.assertEqual([item["tool_use_id"] for item in results], ["ok", "fault", "unexecuted"])
                self.assertEqual([item["is_error"] for item in results], [False, True, True])
                history.append({"role": "user", "content": "再试一次"})
                retry_model = ModelService([response([query("retry")], "tool_use")])
                await agent_loop(history, cast(AsyncAnthropic, retry_model), tools, "test-model")
                self.assertEqual(len(service.calls), 2)

    async def test_credentials_never_reach_history_model_or_final_answer(self):
        key = "secret with/slash"
        urls = [f"https://mcp.example.test/mcp?key={quote_plus(key)}",
                "https://user:password@model.example.test/v1"]
        class LeakyService(FaultMapService):
            async def list_tools(self, *, cursor=None):
                listed = await super().list_tools(cursor=cursor)
                listed.tools[0].description += " ".join(urls)
                return listed

        service = LeakyService([map_result({"name": "西湖", "url": urls[0]}, text=key)])
        tools = AmapTools(cast(Client, service), api_key=key)
        await tools.discover()
        model = ModelService([
            response([query("safe-id")], "tool_use"),
            response([{"type": "text", "text": "西湖：" + " ".join(urls) + " " + key}]),
        ])
        history = [{"role": "user", "content": "查询西湖，错误连接地址是 " + urls[0]}]

        answer = await agent_loop(history, cast(AsyncAnthropic, model), tools, "test-model")

        visible = json.dumps([history, model.requests, answer], ensure_ascii=False)
        for secret in [key, quote_plus(key), *urls, "user:password"]:
            self.assertNotIn(secret, visible)
        self.assertIn("西湖", answer)

    async def test_sdk_timeouts_and_parameter_errors_remain_recoverable(self):
        request = httpx2.Request("POST", "https://mcp.example.test/mcp")
        for fault, explanation in [
            (httpx2.ReadTimeout("超时", request=request), "超时"),
            (MCPError(REQUEST_TIMEOUT, "Request timed out"), "超时"),
            (MCPError(INVALID_PARAMS, "Invalid params"), "参数"),
        ]:
            with self.subTest(fault=type(fault).__name__, explanation=explanation):
                service = FaultMapService([fault, map_result({"name": "西湖"})])
                tools = AmapTools(cast(Client, service))
                await tools.discover()
                model = ModelService([
                    response([query("fault"), query("ok")], "tool_use"),
                    response([{"type": "text", "text": "西湖已核实，其他地点待核实。"}]),
                ])

                await agent_loop(
                    [{"role": "user", "content": "查询杭州的地点"}],
                    cast(AsyncAnthropic, model), tools, "test-model",
                )

                results = model.requests[1]["messages"][-1]["content"]
                self.assertEqual(len(service.calls), 2)
                self.assertTrue(results[0]["is_error"])
                self.assertIn(explanation, results[0]["content"])
                self.assertFalse(results[1]["is_error"])

    async def test_mixed_failures_preserve_success_and_original_call_ids(self):
        service = FaultMapService([
            map_result({"name": "西湖", "address": "杭州市西湖区"}),
            ValueError("缺少 city"),
            map_result(text="MCP 查询失败", is_error=True),
            map_result({"status": "0", "info": "INVALID_PARAMS", "infocode": "20000"}),
        ])
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([
            response([
                query("ok"), query("arguments", keywords="西湖"),
                query("unknown", name="not_a_map_tool"), query("mcp"), query("amap"),
            ], "tool_use"),
            response([{"type": "text", "text": "已核实西湖位于杭州市西湖区；其他地点待核实。"}]),
        ])

        answer = await agent_loop(
            [{"role": "user", "content": "查杭州的地点"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        results = model.requests[1]["messages"][-1]["content"]
        self.assertEqual([item["tool_use_id"] for item in results],
                         ["ok", "arguments", "unknown", "mcp", "amap"])
        self.assertEqual([item["is_error"] for item in results], [False, True, True, True, True])
        self.assertEqual(len(service.calls), 4)
        self.assertIn("杭州市西湖区", results[0]["content"])
        self.assertIn("参数", results[1]["content"])
        self.assertIn("待核实", answer)
        self.assertIn("杭州市西湖区", answer)

    async def test_budget_keeps_verified_places_and_pairs_unexecuted_calls(self):
        service = FaultMapService([map_result({
            "pois": [{"id": "west-lake", "name": "西湖", "address": "杭州市西湖区"}]
        })])
        tools = AmapTools(cast(Client, service))
        await tools.discover()
        model = ModelService([response([
            query("done"), query("pending", keywords="灵隐寺", city="杭州")
        ], "tool_use")])
        history = [{"role": "user", "content": "查询杭州西湖和灵隐寺"}]

        answer = await agent_loop(
            history, cast(AsyncAnthropic, model), tools, "test-model", max_tool_calls=1
        )

        self.assertEqual(len(service.calls), 1)
        self.assertEqual(len(model.requests), 1)
        self.assertIn("上限", answer)
        self.assertNotIn("杭州市西湖区", answer)
        self.assertIn("待核实", answer)
        self.assertNotIn("灵隐寺", answer)
        self.assertIn("灵隐寺", str(history))
        results = history[-2]["content"]
        self.assertEqual([item["tool_use_id"] for item in results], ["done", "pending"])
        self.assertFalse(results[0]["is_error"])
        self.assertTrue(results[1]["is_error"])

        history.append({"role": "user", "content": "先只说明已经查到的地点"})
        followup = ModelService([response([{"type": "text", "text": "西湖位于杭州市西湖区。"}])])
        await agent_loop(history, cast(AsyncAnthropic, followup), tools, "test-model")
        self.assertEqual(followup.requests[0]["messages"][-1]["role"], "user")
        self.assertEqual(followup.requests[0]["messages"][-2]["role"], "assistant")

    async def test_budgets_accumulate_across_rounds_and_reset_for_next_request(self):
        for limits, expected_calls, expected_rounds in [
            ({"max_tool_calls": 3}, 3, 2), ({"max_rounds": 2}, 4, 2),
        ]:
            with self.subTest(limits=limits):
                service = FaultMapService([map_result({"name": "西湖"}) for _ in range(5)])
                tools = AmapTools(cast(Client, service))
                await tools.discover()
                model = ModelService([
                    response([query(f"r{index}-a"), query(f"r{index}-b")], "tool_use")
                    for index in range(3)
                ])
                history = [{"role": "user", "content": "不停查询西湖"}]

                answer = await agent_loop(
                    history, cast(AsyncAnthropic, model), tools, "test-model", **limits
                )

                self.assertEqual(len(service.calls), expected_calls)
                self.assertEqual(len(model.requests), expected_rounds)
                self.assertIn("上限", answer)
                self.assertNotIn("西湖", answer)
                self.assertIn("西湖", str(history))
                self.assertIn("待核实", answer)
                history.append({"role": "user", "content": "新的请求，只查一次"})
                followup = ModelService([
                    response([query("new")], "tool_use"),
                    response([{"type": "text", "text": "西湖已核实。"}]),
                ])
                await agent_loop(history, cast(AsyncAnthropic, followup), tools, "test-model", **limits)
                self.assertEqual(len(service.calls), expected_calls + 1)

    async def test_timeout_cancels_tool_without_losing_success_in_same_batch(self):
        cancelled = asyncio.Event()
        class SlowService(FaultMapService):
            async def call_tool(self, name, arguments):
                if arguments["keywords"] == "慢查询":
                    self.calls.append((name, arguments))
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                return await super().call_tool(name, arguments)

        service = SlowService([map_result({"name": "西湖"})])
        tools = AmapTools(cast(Client, service), tool_timeout=0.01)
        await tools.discover()
        model = ModelService([
            response([query("slow", keywords="慢查询", city="杭州"), query("ok")], "tool_use"),
            response([{"type": "text", "text": "已查到西湖；慢查询超时，待核实。"}]),
        ])

        answer = await agent_loop(
            [{"role": "user", "content": "查询杭州的地点"}],
            cast(AsyncAnthropic, model), tools, "test-model",
        )

        self.assertTrue(cancelled.is_set())
        results = model.requests[1]["messages"][-1]["content"]
        self.assertEqual([item["tool_use_id"] for item in results], ["slow", "ok"])
        self.assertEqual([item["is_error"] for item in results], [True, False])
        self.assertIn("超时", results[0]["content"])
        self.assertIn("西湖", answer)


if __name__ == "__main__":
    unittest.main()
