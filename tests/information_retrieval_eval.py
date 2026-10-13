"""显式真实模型验收：CLI/Web 公开入口，地图和网页为固定 MCP 返回。

证据保存回答、公开工具轨迹和用量，不保存服务地址或凭据。
普通规划使用两个已指定地点；官方政策查询在这个场景不属于所需资料。
"""
import argparse
import asyncio
from contextlib import asynccontextmanager, redirect_stdout
from datetime import datetime
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from mcp import Client
from mcp.server import MCPServer

from travel_app.agent import SYSTEM, run_cli
from travel_app.amap_mcp import AmapTools
from travel_app.exa_mcp import ExaTools
from travel_app.travel_tools import TravelTools
from travel_app.store import Store
from travel_app.travel_dates import model_date_context, turn_date_context
from travel_app.web import Runtime, create_app


NOW = datetime.fromisoformat("2026-10-13T10:00:00+08:00")
PLAN = "2026年10月17日北京一日游，上午首都博物馆，下午玉渊潭公园，不安排餐饮，只坐公交。给活动顺序和两地之间的交通耗时。"
CASES = {
    "default": [PLAN],
    "opening": ["首都博物馆几点开门？"],
    "verification": ["帮我确认首都博物馆2026年10月20日是否开放。", "那10月21日呢？",
                     PLAN, "日期改成2026年10月18日，其他安排不变。"],
    "guide": ["搜一下北京博物馆周末有什么玩法，推荐一个玩法并注明资料来源。"],
    "booking": ["找首都博物馆预约入口。", PLAN, "日期改成2026年10月18日，其他安排不变。"],
    "conflict": ["已有资料写首都博物馆常规周一闭馆，另一个来源说周一开放，两份资料矛盾。我打算2026年10月19日周一上午去，不替换地点，请给参观安排建议。"],
    "restart": ["帮我确认首都博物馆2026年10月20日是否开放。", PLAN],
    "resume": [PLAN],
}


def services():
    maps, sources = MCPServer("固定地图返回"), MCPServer("固定网页返回")
    source_calls = []

    @maps.tool(name="maps_text_search")
    def place(keywords: str, city: str) -> dict:
        park = "玉渊潭" in keywords
        return {"pois": [{"id": "park" if park else "museum", "name": "玉渊潭公园" if park else "首都博物馆",
            "address": "北京市西三环中路10号" if park else "北京市复兴门外大街16号",
            "location": "116.320,39.916" if park else "116.341,39.906", "type": "公园" if park else "博物馆"}]}

    @maps.tool(name="maps_search_detail")
    def detail(id: str) -> dict:
        return place("玉渊潭" if id == "park" else "首都博物馆", "北京")

    @maps.tool(name="maps_direction_transit_integrated")
    def transit(origin: str, destination: str, city: str, cityd: str = "北京") -> dict:
        return {"route": {"origin": origin, "destination": destination,
            "transits": [{"duration": "1800", "distance": "4500",
                "segments": [{"bus": {"buslines": [{"name": "控制公交1路", "departure_stop": {"name": "博物馆站"},
                    "arrival_stop": {"name": "公园站"}}]}}]}]}}

    @maps.tool(name="maps_weather")
    def weather(city: str) -> dict:
        return {"forecasts": [{"city": "北京", "casts": [
            {"date": f"2026-10-{day}", "dayweather": "多云", "nightweather": "多云"} for day in (17, 18, 19, 20, 21)]}]}

    @sources.tool(name="web_search_exa")
    def search(query: str, objective: str) -> str:
        source_calls.append({"name": "web_search_exa", "query": query, "objective": objective})
        if "玩法" in query or "攻略" in query:
            return "Title: 周末博物馆玩法\nURL: https://guide.example.test/weekend\nPublished: 2026-10-01\nHighlights:\n推荐按青铜器主题串联展厅。"
        return "Title: 首都博物馆参观须知\nURL: https://museum.example.test/visit\nPublished: 2026-10-01\nHighlights:\n常规09:00开门、17:00闭馆，周一闭馆；需预约。预约入口：https://museum.example.test/book。"

    @sources.tool(name="web_fetch_exa")
    def fetch(urls: list[str], maxCharacters: int = 3000) -> str:
        source_calls.append({"name": "web_fetch_exa", "urls": urls})
        return "\n\n---\n\n".join(
            f"# 周末博物馆玩法\nURL: {url}\n\n推荐按青铜器主题串联展厅，不用追求参观全部展厅。"
            if "guide.example.test" in url else
            f"# 首都博物馆参观须知\nURL: {url}\n\n本页为控制资料，来源身份未确认。常规09:00–17:00，周一闭馆。2026年10月20日临时闭馆，10月21日开放。预约入口：https://museum.example.test/book。只载明需预约，未提供余票、预约凭证或小程序操作步骤。" for url in urls)

    return maps, sources, source_calls


class ObservedModel:
    def __init__(self, real):
        self.real, self.requests, self.messages = real, [], self

    async def create(self, **kwargs):
        started = time.perf_counter()
        result = await self.real.messages.create(**kwargs)
        self.requests.append({"duration_ms": round((time.perf_counter() - started) * 1000),
            "stop_reason": result.stop_reason, "usage": result.usage.model_dump(mode="json"),
            "content": [block.model_dump(mode="json") for block in result.content]})
        return result


@asynccontextmanager
async def serving(directory, model, maps, sources):
    @asynccontextmanager
    async def resources():
        async with Client(maps) as map_client, Client(sources) as source_client:
            amap = AmapTools(map_client)
            await amap.discover()
            exa = ExaTools(source_client, redact=amap.redact, call_interval=0)
            await exa.discover()
            yield Runtime(cast(AsyncAnthropic, model), TravelTools(amap, exa), os.environ["MODEL_ID"])
    app = create_app(directory, resources=resources, clock=lambda: NOW)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def turn(client, text, session=None):
    started = time.perf_counter()
    accepted = await client.post("/api/turns", json={"input": text, "session_id": session})
    accepted.raise_for_status()
    identity = accepted.json()
    async with asyncio.timeout(300):
        while (await client.get("/api/state")).json()["active_turn_id"]:
            await asyncio.sleep(.05)
    snapshot = (await client.get("/api/sessions/" + identity["session_id"])).json()
    tools = [tool for tool in snapshot["tool_calls"] if tool["turn_id"] == identity["turn_id"]]
    arguments = []
    for tool in tools:
        raw = (await client.get("/api/payloads/" + tool["arguments_payload_id"])).json()
        arguments.append({"name": tool["name"], "status": tool["status"], "arguments": raw["content"]})
    result = {"input": text, "duration_ms": round((time.perf_counter() - started) * 1000),
        "turn": snapshot["turns"][-1], "tools": arguments}
    print(json.dumps({"finished": text[:24], "status": result["turn"]["status"]}, ensure_ascii=False), flush=True)
    return identity["session_id"], result


def interrupted_fixture(directory):
    """未完成轮次为历史输入夹具；验收只观察启动恢复后的公开 HTTP 行为。"""
    store = Store(Path(directory))
    try:
        text = "2026年10月20日帮我核实首都博物馆开放情况。"
        identity = store.accept(text)
        context = turn_date_context(text, NOW)
        store.observe_request(identity["session_id"], identity["turn_id"], "request.started", {
            "request_id": "fixture-request", "ordinal": 1, "started_at": NOW.isoformat(),
            "input": {"system": SYSTEM + model_date_context(context), "messages": [{"role": "user", "content": text}]}})
        store.observe_tool(identity["session_id"], identity["turn_id"], "tool.completed", {
            "tool_call_id": "fixture-source", "request_id": "fixture-request", "ordinal": 1,
            "name": "web_fetch_exa", "provider": "exa", "finished_at": NOW.isoformat(),
            "arguments": {"urls": ["https://museum.example.test/visit"]}, "result": {
                "is_error": False, "content": json.dumps({"provider": "exa", "retrieved_at": NOW.isoformat(),
                    "document_status": "extraction_result", "sources": [{"url": "https://museum.example.test/visit",
                        "title": "历史控制资料", "content_type": "page_body", "content": "来源身份未核实，2026年10月20日临时闭馆。"}]}, ensure_ascii=False)}})
        return identity["session_id"]
    finally:
        store.close()


async def evaluate(name, real):
    maps, sources, source_calls = services()
    model = ObservedModel(real)
    result = {"case": name, "entry": "Web HTTP", "steps": [], "source_calls": source_calls,
        "requests": model.requests, "fixture_note": "真实模型；地图和网页为固定返回，不能证明真实景点政策。"}
    with tempfile.TemporaryDirectory() as directory:
        session = interrupted_fixture(directory) if name == "resume" else None
        if name == "resume":
            result["history_fixture"] = "未完成轮次包含已保存网页正文；启动时执行真实恢复，历史状态未核实。"
        for index, text in enumerate(CASES[name]):
            # restart 场景每轮重新启动服务，其他场景在一个服务内保留会话。
            if name == "restart":
                async with serving(directory, model, maps, sources) as client:
                    session, step = await turn(client, text, session)
                result["steps"].append(step)
            elif index == 0:
                async with serving(directory, model, maps, sources) as client:
                    for prompt in CASES[name]:
                        session, step = await turn(client, prompt, session)
                        result["steps"].append(step)
                break
    return result


async def cli(real):
    maps, sources, source_calls = services()
    model = ObservedModel(real)
    @asynccontextmanager
    async def model_context(**kwargs):
        yield model
    output = io.StringIO()
    with patch("travel_app.agent.Client", side_effect=lambda *args, **kwargs: Client(maps)), \
         patch("travel_app.travel_tools.Client", side_effect=lambda *args, **kwargs: Client(sources)), \
         patch("travel_app.agent.AsyncAnthropic", side_effect=model_context), \
         patch("builtins.input", side_effect=[PLAN, "exit"]), redirect_stdout(output):
        exit_code = await run_cli("controlled-map-key", clock=lambda: NOW)
    return {"case": "cli", "entry": "run_cli", "exit_code": exit_code, "answer": output.getvalue(),
        "source_calls": source_calls, "requests": model.requests}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=[*CASES, "cli"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
    logging.disable(logging.CRITICAL)
    os.environ["CONTEXT_COMPACTION_ENABLED"] = "false"
    result = {"case": args.case}
    try:
        async with AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY") or None,
                auth_token=None if os.getenv("ANTHROPIC_BASE_URL") else os.getenv("ANTHROPIC_AUTH_TOKEN") or None,
                base_url=os.getenv("ANTHROPIC_BASE_URL") or None, timeout=60, max_retries=0) as real:
            result = await cli(real) if args.case == "cli" else await evaluate(args.case, real)
    except Exception as error:
        result["error_type"] = type(error).__name__
    text = json.dumps(result, ensure_ascii=False, indent=2)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "AMAP_MAPS_API_KEY"):
        value = os.getenv(key)
        if value:
            text = text.replace(value, "[redacted]")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text + "\n")
    print(json.dumps({"case": args.case, "error_type": result.get("error_type"), "output": str(args.output)}, ensure_ascii=False))
    return 1 if result.get("error_type") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
