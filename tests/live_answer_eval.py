"""显式启动真实模型验收：生产装配、隔离 SQLite，不替换或延迟模型输出。"""
import argparse
from contextlib import asynccontextmanager, ExitStack
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from anthropic import AsyncAnthropic
import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import Request
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from travel_app.planning import configured_planning
from travel_app.sessions import Runtime, create_app
from live_answer_observation import Recorder, ObservedClient, ObservedSSE

PROMPTS = {
    "plain": "联合验收：只依据我给定的信息，用中文详细写一份居家旅行行前准备清单，不查询外部资料，不需要实时价格、天气或开放信息。请以标题‘联合验收旅行’开头，分成十个主题，每个主题至少写五条具体建议，最后附一张十行的检查表。全文约两千字，直接给出完整正文。",
    "query": "请安排公历2026年11月14日杭州一天旅行，两位成人从杭州东站出发，晚上回杭州东站。请实际查找西湖和中国茶叶博物馆的地点资料、两地公共交通及开放／预约信息，区分未核实内容，给出含交通和来源的详细中文旅行行程。请用简短中文说明正在查什么；不要把准备查询写成查询成功。",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--scenario", choices=tuple(PROMPTS), default="plain")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=True)
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    recorder = Recorder()
    coordination = {}
    environment = {"kind": "configured-model-answer-acceptance", "run_id": uuid4().hex,
                   "scenario": args.scenario, "prompt": PROMPTS[args.scenario],
                   "preparation_prompt": "我会接着提供旅行需求，请简短回复收到，不查询资料。",
                   "model": os.environ["MODEL_ID"], "python": platform.python_version(),
                   "anthropic_sdk": version("anthropic"),
                   "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   "provider": urlsplit(os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").hostname}

    def write(name, value):
        (args.evidence_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    @asynccontextmanager
    async def resources():
        async with configured_planning() as ready:
            recorder.redact = ready.tools.redact
            try:
                yield Runtime(cast(AsyncAnthropic, ObservedClient(ready.client, recorder)), ready.tools, ready.model)
            finally:
                write("server-timeline.json", recorder.events)

    with ExitStack() as stack:
        directory = args.data_dir or Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="potato-live-answer-")))
        app = create_app(directory, resources=resources, static_dir=Path(directory) / "no-static")

        @app.get("/__acceptance__/environment")
        async def metadata():
            return {**environment, "server_time": datetime.now(timezone.utc).isoformat()}

        @app.get("/__acceptance__/coordination")
        async def readiness():
            return coordination

        @app.post("/__acceptance__/coordination/{key}")
        async def mark(key: str, request: Request):
            # 仅在输入前协调导航、结束后串行复制；不参与模型流或生产状态推进。
            coordination[key] = await request.json()
            recorder.record("coordination", key)
            write("coordination.json", coordination)
            return coordination

        @app.post("/__acceptance__/ios-evidence")
        async def ios_evidence(request: Request):
            write("ios-timeline.json", await request.json())
            return {"saved": True}

        @app.post("/__acceptance__/capture")
        async def capture():
            # 只沿公开HTTP关联SDK调用，不读取私有运行状态或数据库。
            async with httpx.AsyncClient(base_url="https://localhost:18499", verify=args.cert) as client:
                listing = (await client.get("/api/sessions")).json()
                histories = []
                associations = []
                available = [event for event in recorder.events if event["layer"] == "sdk" and event["kind"] == "request"]
                for session in listing["sessions"]:
                    history = (await client.get(f'/api/sessions/{session["session_id"]}?summary=false&include_messages=false')).json()
                    histories.append({"session": history["session"], "stream_id": history["stream_id"],
                        "turns": [{key: turn.get(key) for key in ("turn_id", "ordinal", "status", "reason", "input", "answer", "answer_source", "created_at", "finished_at", "query_progress")} for turn in history["turns"]],
                        "requests": history["requests"],
                        "tool_calls": [{key: tool.get(key) for key in ("tool_call_id", "request_id", "turn_id", "ordinal", "name", "status", "started_at", "finished_at", "is_error")} for tool in history["tool_calls"]],
                        "events": [{key: event.get(key) for key in ("kind", "cursor", "turn_id", "request_id", "created_at")} for event in history["events"]]})
                    for request in history["requests"]:
                        payload = (await client.get(f'/api/payloads/{request["input_payload_id"]}')).json()
                        fingerprint = recorder.fingerprint(payload["content"])
                        match = next((item for item in available if item["fingerprint"] == fingerprint), None)
                        if match:
                            available.remove(match)
                        associations.append({"session_id": session["session_id"],
                            **{key: request.get(key) for key in ("turn_id", "request_id", "ordinal", "purpose", "answer_phase", "attempt", "status", "started_at", "finished_at")},
                            "call_sequence": match["call_sequence"] if match else None})
                write("histories.json", histories)
                write("request-associations.json", {"associations": associations,
                    "unmatched_sdk_calls": [item["call_sequence"] for item in available]})
                write("snapshot.json", (await client.get("/api/snapshot")).json())
            write("server-timeline.json", recorder.events)
            return {"captured": True, "associations": associations}

        write("environment.json", environment)
        app.mount("/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="page")
        try:
            uvicorn.run(ObservedSSE(app, recorder), host="127.0.0.1", port=18499, ssl_certfile=args.cert,
                        ssl_keyfile=args.key, log_level="warning", access_log=False)
        finally:
            write("server-timeline.json", recorder.events)


if __name__ == "__main__":
    main()
