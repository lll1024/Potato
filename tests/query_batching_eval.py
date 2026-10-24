"""#76：公开 Web 的真实模型对照、固定序列机制观察及真实网页承载。

以 PYTHONPATH 选择被测版本；本脚本和旅行要求、固定资料及延迟保持相同。
不自动判定回答质量，原始请求、返回和回答供逐项语义核对。
"""
import argparse
import asyncio
import copy
from contextlib import AsyncExitStack, asynccontextmanager, redirect_stdout
from datetime import datetime
import gzip
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import cast
from unittest.mock import patch

import httpx
import httpx2
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer

from travel_app.planning import run_cli
from travel_app.retrieval import MapHTTPClient as AmapHTTPClient
from travel_app.retrieval import Retrieval
from travel_app.retrieval import SourceHTTPClient
from travel_app.sessions import Runtime, create_app
from service_fixtures import ModelService, response

NOW = datetime.fromisoformat("2026-10-14T10:00:00+08:00")
WEB = "分别搜索并读取北京博物馆周末玩法、北京公园秋季玩法、北京胡同散步玩法三份正文，每份给具体玩法、建议时长、限制及实际来源链接。不安排交通餐饮，不额外核实。"
PLAN = "2026年10月17日北京一日游，上午首都博物馆，下午玉渊潭公园，不安排餐饮，只坐公交。从首都博物馆开始。分别搜索并读取两处的玩法正文，给具体看点、时长、限制、来源，以及两地公交的线路、上下车站、耗时和距离。"
CASES = {
    "web": [WEB],
    "mixed": [PLAN],
    "dependency": ["2026年10月17日北京一日游，不安排餐饮，只坐公交。先搜索北京博物馆候选玩法，读取候选正文后，选择资料明确有青铜器专题的一家（只去一家）；下午玉渊潭公园。获取所选博物馆与公园定位，再给两地公交衔接、具体玩法时长限制和来源，不猜地点、链接或坐标。"],
    "adjustment": [PLAN, "日期改成2026年10月18日，两地之间改成只步行，其他要求和地点不变，保留已有玩法细节、限制和来源，更新必要交通及天气，不重复询问。"],
    "ambiguous": ["2026年10月17日北京，从星光书店去玉渊潭公园，只坐公交。我没有确认书店分店，先帮我确定实际起终点，不猜分店或坐标。"],
}
DOCUMENTS = {
    "museum": ("首都博物馆周末玩法", "青铜器专题可按器形串联参观，建议90分钟；资料未说明具体展厅编号。常规09:00–17:00、周一闭馆，未来当日开放待旅行者核实。需预约，入口 https://museum.example.test/book；未提供余票和入场操作。"),
    "other": ("另一家博物馆玩法", "以书画主题串联参观，建议60分钟；资料未提到青铜器专题，也未提供开放和预约规则。"),
    "park": ("玉渊潭公园秋季玩法", "沿湖观察秋色，建议75分钟；资料没有实时叶色情况，也没有门票与开放规则。"),
    "hutong": ("北京胡同散步玩法", "从胡同街口慢行观察街巷，建议45分钟；不要进入私人院落，资料未指定餐馆。"),
}
URLS = {key: f"https://guide.example.test/{key}" for key in DOCUMENTS}


class Services:
    def __init__(self, delay=0.8):
        self.delay, self.calls = delay, []
        self.maps, self.web = MCPServer("#76 固定地图"), MCPServer("#76 固定网页")

        @self.maps.tool(name="maps_text_search")
        async def place(keywords: str, city: str, citylimit: bool = True) -> dict:
            async with self.observed("map", "maps_text_search", locals()):
                if "星光" in keywords:
                    return {"pois": [
                        {"id": "east", "name": "星光书店东店", "cityname": "北京", "address": "北京市朝阳区测试东路1号", "location": "116.400,39.950"},
                        {"id": "west", "name": "星光书店西店", "cityname": "北京", "address": "北京市海淀区测试西路2号", "location": "116.300,39.900"}]}
                park = "玉渊潭" in keywords
                return {"pois": [{"id": "park" if park else "museum", "name": "玉渊潭公园" if park else "首都博物馆",
                    "cityname": "北京", "address": "北京市西三环中路10号" if park else "北京市复兴门外大街16号",
                    "location": "116.320,39.916" if park else "116.341,39.906", "type": "公园" if park else "博物馆"}]}

        @self.maps.tool(name="maps_direction_transit_integrated")
        async def transit(origin: str, destination: str, city: str, cityd: str) -> dict:
            async with self.observed("map", "maps_direction_transit_integrated", locals()):
                return {"route": {"origin": origin, "destination": destination, "transits": [{"duration": "1800", "distance": "4500",
                    "segments": [{"bus": {"buslines": [{"name": "控制公交1路", "departure_stop": {"name": "博物馆站"}, "arrival_stop": {"name": "公园站"}}]}}]}]}}

        @self.maps.tool(name="maps_direction_walking")
        async def walking(origin: str, destination: str) -> dict:
            async with self.observed("map", "maps_direction_walking", locals()):
                return {"route": {"origin": origin, "destination": destination, "paths": [{"duration": "4200", "distance": "4500"}]}}

        @self.maps.tool(name="maps_weather")
        async def weather(city: str) -> dict:
            async with self.observed("map", "maps_weather", locals()):
                return {"forecasts": [{"city": "北京", "casts": [
                    {"date": "2026-10-17", "dayweather": "多云", "nightweather": "多云"},
                    {"date": "2026-10-18", "dayweather": "小雨", "nightweather": "小雨"}]}]}

        @self.web.tool(name="web_search_exa")
        async def search(query: str, objective: str) -> str:
            async with self.observed("web", "web_search_exa", locals()):
                keys = (["museum", "other"] if "候选" in query or "青铜" in query else
                        ["park"] if "公园" in query or "玉渊潭" in query else
                        ["hutong"] if "胡同" in query else ["museum"])
                return "\n\n".join(f"Title: {DOCUMENTS[key][0]}\nURL: {URLS[key]}\nHighlights:\n玩法详情、时长及限制请读取正文。来源身份未核实。" for key in keys)

        @self.web.tool(name="web_fetch_exa")
        async def fetch(urls: list[str], maxCharacters: int = 3000) -> str:
            async with self.observed("web", "web_fetch_exa", locals()):
                return "\n\n---\n\n".join(f"# {DOCUMENTS[key][0]}\nURL: {url}\n\n来源身份未核实。{DOCUMENTS[key][1][:maxCharacters]}"
                    if (key := next((k for k, u in URLS.items() if u == url), None)) else f"URL: {url}\n未取得正文。" for url in urls)

    @asynccontextmanager
    async def observed(self, provider, name, arguments):
        item = {"provider": provider, "name": name, "arguments": {k: v for k, v in arguments.items() if k != "self"}, "start": time.perf_counter()}
        self.calls.append(item)
        try:
            await asyncio.sleep(self.delay)
            yield
        finally:
            item["end"] = time.perf_counter()

    @asynccontextmanager
    async def resources(self, model, live_web=False):
        async with AsyncExitStack() as stack:
            map_app = self.maps.streamable_http_app(json_response=True, stateless_http=True)
            await stack.enter_async_context(map_app.router.lifespan_context(map_app))
            map_http = await stack.enter_async_context(AmapHTTPClient(transport=httpx2.ASGITransport(app=map_app)))
            map_client = await stack.enter_async_context(Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=map_http)))
            amap = Retrieval(map_client, http_client=map_http)
            await amap.discover()
            if live_web:
                source_http = await stack.enter_async_context(SourceHTTPClient(timeout=30))
                source_url = "https://mcp.exa.ai/mcp"
            else:
                source_app = self.web.streamable_http_app(json_response=True, stateless_http=True)
                await stack.enter_async_context(source_app.router.lifespan_context(source_app))
                source_http = await stack.enter_async_context(SourceHTTPClient(transport=httpx2.ASGITransport(app=source_app)))
                source_url = "http://127.0.0.1:8000/mcp"
            source_client = await stack.enter_async_context(Client(streamable_http_client(source_url, http_client=source_http), read_timeout_seconds=30))
            amap = Retrieval(map_client, source_client=source_client, http_client=map_http)
            await amap.discover()
            yield Runtime(cast(AsyncAnthropic, model), amap, os.environ["MODEL_ID"])


class ObservedModel:
    """CLI 无持久轨迹，保留实际请求及真实返回供同规则核对。"""
    def __init__(self, real):
        self.real, self.requests, self.messages = real, [], self

    async def create(self, **parameters):
        item = {"input": copy.deepcopy(parameters)}
        self.requests.append(item)
        started = time.perf_counter()
        try:
            result = await self.real.messages.create(**parameters)
            item["response"] = result.model_dump(mode="json")
            return result
        except Exception as error:
            item["error_type"] = type(error).__name__
            raise
        finally:
            item["duration_ms"] = (time.perf_counter() - started) * 1000


async def turn(client, text, session=None):
    start = time.perf_counter()
    accepted = await client.post("/api/turns", json={"input": text, "session_id": session})
    accepted.raise_for_status()
    identity = accepted.json()
    async with asyncio.timeout(600):
        while (await client.get("/api/state")).json()["active_turn_id"]:
            await asyncio.sleep(.05)
    duration = (time.perf_counter() - start) * 1000
    snapshot = (await client.get("/api/sessions/" + identity["session_id"])).json()
    requests = [r for r in snapshot["requests"] if r["turn_id"] == identity["turn_id"]]
    tools = [t for t in snapshot["tool_calls"] if t["turn_id"] == identity["turn_id"]]
    payloads = {}
    for item in [*requests, *tools]:
        for key, value in item.items():
            if key.endswith("_payload_id") and value:
                payloads[value] = (await client.get("/api/payloads/" + value)).json()["content"]
    events = [e for e in snapshot["events"] if e["turn_id"] == identity["turn_id"]]
    return identity["session_id"], {"input": text, "wait_ms": duration, "turn": snapshot["turns"][-1], "requests": requests,
        "tools": tools, "events": events, "payloads": payloads,
        "metrics": {"model_requests": len(requests), "model_ms": sum(r.get("duration_ms") or 0 for r in requests),
            "tool_calls": sum(bool(t.get("call_started")) for t in tools),
            "tool_wait_ms_sum": sum(t.get("wait_duration_ms") or 0 for t in tools),
            "tool_execution_ms_sum": sum(t.get("call_duration_ms") or 0 for t in tools),
            "summaries": sum(r["purpose"] == "context_summary" for r in requests),
            "retries": sum(e["kind"] == "request.retry_started" for e in events),
            "failures": [t.get("failure_category") for t in tools if t["status"] == "failed"]}}


def fixed_model(case):
    blocks = [{"type": "tool_use", "id": f"web-{i}", "name": "web_search_exa", "input": {"query": f"北京{topic}玩法", "objective": "取得玩法来源"}}
        for i, topic in enumerate(["博物馆", "公园", "胡同"] if case == "mechanism" else
                                 ["博物馆", "公园", "胡同", "天坛", "北海", "颐和园", "国子监", "景山", "香山", "奥林匹克公园"])]
    if case == "mechanism":
        blocks += [{"type": "tool_use", "id": f"map-{i}", "name": "maps_text_search", "input": {"keywords": name, "city": "北京"}}
                   for i, name in enumerate(["首都博物馆", "玉渊潭公园"])]
    return ModelService([response(blocks, "tool_use"), response([{"type": "text", "text": "固定序列机制观察，不评估模型策略或回答质量。"}])])


async def evaluate(args, model):
    services = Services()
    result = {"case": args.case, "version": args.version, "model": os.environ["MODEL_ID"],
        "now": NOW.isoformat(), "delay_seconds": services.delay, "entry": "Web /api/turns + MCP/HTTP + SQLite",
        "context_compaction_enabled": os.getenv("CONTEXT_COMPACTION_ENABLED", "false").lower() == "true",
        "scope": "真实模型和固定资料；不是景点政策或真实服务承载证据。" if args.case in CASES else "固定模型序列；不是模型行为证据。",
        "steps": [], "service_calls": services.calls}
    with tempfile.TemporaryDirectory() as directory:
        @asynccontextmanager
        async def resources():
            async with services.resources(model, live_web=args.case == "live-load") as runtime:
                yield runtime
        app = create_app(directory, resources=resources, clock=lambda: NOW)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                session = None
                for prompt in CASES.get(args.case, ["运行固定必要查询序列，观察调度。"]):
                    session, step = await turn(client, prompt, session)
                    result["steps"].append(step)
                    print(json.dumps({"case": args.case, "version": args.version, "status": step["turn"]["status"], "wait_ms": round(step["wait_ms"]), **step["metrics"]}, ensure_ascii=False), flush=True)
    return result


async def cli(model):
    model = ObservedModel(model)
    services = Services()
    output = io.StringIO()
    @asynccontextmanager
    async def model_context(**kwargs):
        yield model
    async with services.resources(model) as runtime:
        @asynccontextmanager
        async def retrieval(*args, **kwargs):
            yield runtime.tools
        with patch('builtins.input', side_effect=[WEB, 'exit']), redirect_stdout(output):
            exit_code = await run_cli("controlled-key", clock=lambda: NOW, retrieval_factory=retrieval, model_client_factory=model_context)
    return {"case": "cli", "exit_code": exit_code, "answer": output.getvalue(), "service_calls": services.calls, "requests": model.requests}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=[*CASES, "mechanism", "live-load", "cli"], required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--configured-context", action="store_true", help="补测沿用 .env 的上下文维护配置；默认对照单独观察短任务策略与调度。")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
    logging.disable(logging.CRITICAL)
    if not args.configured_context:
        os.environ["CONTEXT_COMPACTION_ENABLED"] = "false"
    result = {"case": args.case, "version": args.version}
    try:
        if args.case in ("mechanism", "live-load"):
            result = await evaluate(args, fixed_model(args.case))
        else:
            async with AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY") or None,
                    auth_token=None if os.getenv("ANTHROPIC_BASE_URL") else os.getenv("ANTHROPIC_AUTH_TOKEN") or None,
                    base_url=os.getenv("ANTHROPIC_BASE_URL") or None, timeout=60, max_retries=0) as model:
                result = await cli(model) if args.case == "cli" else await evaluate(args, model)
    except Exception as error:
        result["error_type"] = type(error).__name__
    text = json.dumps(result, ensure_ascii=False, indent=2)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "AMAP_MAPS_API_KEY"):
        if value := os.getenv(key):
            text = text.replace(value, "[redacted]")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt") as stream:
        stream.write(text + "\n")
    print(json.dumps({"output": str(args.output), "error_type": result.get("error_type")}, ensure_ascii=False), flush=True)
    return 1 if result.get("error_type") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
