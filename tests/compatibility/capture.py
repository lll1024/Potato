"""固定合成外部响应，保留 HTTP/SSE、CLI 和进程恢复的真实协作。"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile

import httpx
from fastapi.responses import JSONResponse

from process_fixtures import ExternalServices, free_port
from http_fixtures import wait_finished


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/deep-modules"
OLD = "2026-10-24 杭州旅行，预算3000元，保留西湖酒店。我一直不吃辣。" + "中" * 3900


def message(content):
    return {"id": "synthetic-response", "type": "message", "role": "assistant", "model": "test-model",
            "content": content, "stop_reason": "tool_use" if content[0]["type"] == "tool_use" else "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 2}}


class ScenarioServices(ExternalServices):
    def __init__(self):
        self.mode = "normal"
        super().__init__(model_handler=self.respond)
        self.queries = []
        # 同一个本机 MCP 端点提供确定性网页声明；其调用仍经真实 Exa adapter。
        # maps 工具已由 ExternalServices 注册。
        from mcp.server import MCPServer
        maps = MCPServer("基线资料服务")

        @maps.tool(name="maps_text_search")
        async def search(keywords: str, city: str) -> str:
            self.map_calls.append((keywords, city))
            if self.mode == "stop":
                self.entered.set()
                await self.release.wait()
            return "西湖位于杭州；早餐价格120元，未预订，适用日期待核实。"

        @maps.tool(name="web_search_exa")
        async def web_search(query: str, objective: str) -> str:
            self.queries.append({"query": query, "objective": objective})
            return "Title: 西湖资料\nURL: https://example.test/west-lake\nPublished: N/A\nAuthor: N/A\nHighlights:\n来源未核实，预约入口见原网页。"

        @maps.tool(name="web_fetch_exa")
        async def fetch(urls: list[str]) -> str:
            return "# 西湖资料\nURL: https://example.test/west-lake\n\n早餐价格120元，未预订。"

        # 复用相同外部 HTTP 服务生命周期，不替换应用内任何规划或存储代码。
        mcp_app = maps.streamable_http_app(json_response=True, stateless_http=True)
        self.app.router.routes = [route for route in self.app.router.routes if getattr(route, "path", None) != "/maps"]
        self.app.mount("/maps", mcp_app)
        @asynccontextmanager
        async def lifespan(_app):
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        self.app.router.lifespan_context = lifespan

    async def respond(self, data):
        if data["system"].startswith("旅行者偏好提取"):
            source = json.loads(data["messages"][0]["content"])
            item = next((item for item in source["new_inputs"] if "我一直不吃辣" in item["input"]), None)
            changes = [{"operation": "add", "category": "diet", "content": "不吃辣",
                        "source_turn_id": item["turn_id"], "evidence": "我一直不吃辣"}] if item else []
            return message([{"type": "text", "text": json.dumps({"changes": changes}, ensure_ascii=False)}])
        if "整理旅行状态" in data["system"]:
            return message([{"type": "text", "text": json.dumps({
                "travel_conditions": {"budget": "3000元", "hotel": "西湖酒店"}, "confirmed_itinerary": [],
                "corrections": [], "unresolved": ["早餐价格待核实"],
                "sources": [{"record_id": "message:1", "verification": "用户要求"},
                            {"record_id": "message:2", "verification": "未预订"}]}, ensure_ascii=False)}])
        if self.mode == "failure":
            return JSONResponse(status_code=401, content={"error": {"type": "authentication_error", "message": "synthetic authentication failure"}})
        if self.mode == "restart":
            self.entered.set()
            await self.release.wait()
        last = data["messages"][-1]["content"]
        if isinstance(last, str) and last.startswith("地图"):
            tools = [{"type": "tool_use", "id": "map-1", "name": "maps_text_search",
                      "input": {"keywords": "西湖", "city": "杭州"}}]
            if self.mode == "normal":
                tools.append({"type": "tool_use", "id": "web-1", "name": "web_search_exa",
                              "input": {"query": "西湖", "objective": "取得游玩资料"}})
            return message(tools)
        return message([{"type": "text", "text": "保留西湖酒店，早餐价格待核实，尚未预订。"}])


@asynccontextmanager
async def service(root, directory, external, clock_file, entrypoint=Path(__file__).with_name("entrypoint.py")):
    port = free_port()
    child = await asyncio.create_subprocess_exec(sys.executable, str(entrypoint),
        "web", str(root), external, str(directory), str(port), str(clock_file), cwd=directory,
        env={**os.environ, "PYTHONPATH": str(root)}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    assert child.stdout is not None
    drain = asyncio.create_task(child.stdout.read())
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            async def ready():
                while child.returncode is None:
                    try:
                        if (await client.get("/api/state")).status_code == 200:
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.02)
                raise AssertionError((await drain).decode())
            await asyncio.wait_for(ready(), 15)
            yield child, client
    finally:
        if child.returncode is None:
            child.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(child.wait(), 10)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
            await drain


async def snapshot(client, session):
    saved = (await client.get("/api/sessions/" + session)).json()
    payload_ids = set()
    for row in [*saved["turns"], *saved["requests"], *saved["tool_calls"]]:
        payload_ids.update(value for key, value in row.items() if key.endswith("payload_id") and value)
    payloads = {key: (await client.get("/api/payloads/" + key)).json() for key in sorted(payload_ids)}
    events = []
    async with client.stream("GET", "/api/events?after=0") as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data:"):
                event = json.loads(line[5:])
                if event.get("session_id") == session:
                    events.append(event)
                    if event["event_id"] == saved["events"][-1]["event_id"]:
                        break
    assert events == [{"schema_version": 1, **event} for event in saved["events"]]
    return {"snapshot": saved, "payloads": payloads, "sse": events}


async def submit(client, text, session=None):
    response = await client.post("/api/turns", json={"input": text, "session_id": session,
                                                    "submission_id": text[:30]})
    assert response.status_code == 202, response.text
    identity = response.json()
    await asyncio.wait_for(wait_finished(client, identity["session_id"], identity["turn_id"]), 15)
    assert (await client.get("/api/submissions/" + identity["submission_id"])).json() == identity
    return identity


async def capture(root, directory, *, entrypoint=Path(__file__).with_name("entrypoint.py")):
    directory.mkdir(parents=True, exist_ok=True)
    clock_file = directory / "clock.txt"
    clock_file.write_text("10000")
    external = ScenarioServices()
    result = {}
    async with external.serving() as url:
        async with service(root, directory, url, clock_file, entrypoint) as (_, client):
            normal = await submit(client, "地图查询杭州西湖，2026-10-24出行")
            result["normal"] = await snapshot(client, normal["session_id"])
            # 真实压缩产生有效上下文版本，再由后台闲置流程生成偏好。
            compacted = await submit(client, OLD)
            await submit(client, "继续安排交通", compacted["session_id"])
            clock_file.write_text("13601")
            async def preferences_saved():
                while True:
                    value = (await client.get("/api/preferences")).json()
                    if value["preferences"] and not value["processing"]:
                        return value
                    await asyncio.sleep(.02)
            result["preferences"] = await asyncio.wait_for(preferences_saved(), 10)
            result["compacted"] = await snapshot(client, compacted["session_id"])
            external.mode = "failure"
            failed = await submit(client, "模型失败")
            result["failure"] = await snapshot(client, failed["session_id"])
            # 此场景在实际调用中停止；等待既有 0.4s 地图间隔耗尽，避免偶然多出排队事件。
            await asyncio.sleep(.5)
            external.mode = "stop"
            stopped = (await client.post("/api/turns", json={"input": "地图停止"})).json()
            await asyncio.wait_for(external.entered.wait(), 10)
            result["stop_response"] = (await client.post("/api/turns/" + stopped["turn_id"] + "/stop")).json()
            result["stopping"] = (await client.get("/api/state")).json()
            assert result["stopping"]["active_turn_id"] == stopped["turn_id"]
            result["busy"] = {"status": (await client.post("/api/turns", json={"input": "忙碌时拒绝"})).status_code}
            external.release.set()
            await asyncio.wait_for(wait_finished(client, stopped["session_id"], stopped["turn_id"]), 10)
            result["stop"] = await snapshot(client, stopped["session_id"])
        external.entered.clear()
        external.release.clear()
        external.mode = "restart"
        async with service(root, directory, url, clock_file, entrypoint) as (child, client):
            interrupted = (await client.post("/api/turns", json={"input": "进程中断", "submission_id": "interrupted"})).json()
            await asyncio.wait_for(external.entered.wait(), 10)
            result["before_kill"] = await snapshot(client, interrupted["session_id"])
            child.kill()
            await child.wait()
            external.release.set()
        calls = len(external.model_calls)
        external.mode = "normal"
        async with service(root, directory, url, clock_file, entrypoint) as (_, client):
            result["recovered"] = await snapshot(client, interrupted["session_id"])
            assert len(external.model_calls) == calls, "重启不得重放模型请求"
            assert (await client.get("/api/submissions/interrupted")).json() == interrupted
            await submit(client, "恢复后继续", compacted["session_id"])
            result["continued"] = await snapshot(client, compacted["session_id"])
        # 调用真实根 CLI，两轮同进程续聊；CLI 不写 Web 历史。
        cli = await asyncio.create_subprocess_exec(sys.executable, str(entrypoint),
            "cli", str(root), url, cwd=directory, env={**os.environ, "PYTHONPATH": str(root)},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(cli.communicate("地图查询杭州西湖\n保留日期继续\nq\n".encode()), 20)
        assert cli.returncode == 0, stderr.decode()
        result["cli"] = {"returncode": cli.returncode, "stdout": stdout.decode()}
    result["model_requests"] = external.model_calls
    result["map_calls"] = external.map_calls
    result["web_calls"] = external.queries
    return result


def write_json(path, value):
    path.write_bytes(gzip.compress(json.dumps(value, ensure_ascii=False, indent=2).encode(), mtime=0))


async def main():
    parser = argparse.ArgumentParser(description="生成或对照 #80 合成行为基线")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True, help="导出树的真实提交；工作区采集写 working-tree")
    parser.add_argument("--entrypoint", type=Path, default=Path(__file__).with_name("entrypoint.py"),
                        help="历史基线重采集可显式指定固定提交导出的装配脚本；当前树只用公开入口")
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        data = await capture(arguments.root.resolve(), Path(directory), entrypoint=arguments.entrypoint.resolve())
        write_json(arguments.output / "behavior.json.gz", data)
        with sqlite3.connect(Path(directory) / "travel.sqlite3") as database:
            dump = "\n".join(database.iterdump()).encode()
        fixture = gzip.compress(dump, mtime=0)
        (arguments.output / "legacy.sql.gz").write_bytes(fixture)
        source_files = [arguments.root / "agent.py", arguments.root / "web.py",
                        *sorted((arguments.root / "travel_app").rglob("*.py"))]
        digest = hashlib.sha256()
        for path in source_files:
            digest.update(path.relative_to(arguments.root).as_posix().encode())
            digest.update(path.read_bytes())
        manifest = {"source_commit": arguments.source_commit, "source_sha256": digest.hexdigest(),
                    "fixture_sha256": hashlib.sha256(fixture).hexdigest(),
                    "session_id": data["compacted"]["snapshot"]["session"]["session_id"],
                    "interrupted_session_id": data["recovered"]["snapshot"]["session"]["session_id"]}
        (arguments.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
