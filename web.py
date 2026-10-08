"""一个本机服务提供旅行对话页面和同源接口。"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field

from agent import SERVICE_MESSAGE, agent_loop
from amap_http import AmapHTTPClient
from amap_mcp import AmapTools
from limits import TOOL_TIMEOUT
from store import SCHEMA_VERSION, Store


@dataclass
class Runtime:
    client: AsyncAnthropic
    tools: AmapTools
    model: str


@asynccontextmanager
async def configured_resources():
    api_key = os.getenv("AMAP_MAPS_API_KEY", "").strip()
    if not api_key or not os.getenv("MODEL_ID"):
        raise RuntimeError("请配置 AMAP_MAPS_API_KEY 和 MODEL_ID。")
    base_url = os.getenv("ANTHROPIC_BASE_URL") or None
    try:
        async with (
            AmapHTTPClient(timeout=TOOL_TIMEOUT) as http_client,
            Client(streamable_http_client("https://mcp.amap.com/mcp?" + urlencode({"key":api_key}), http_client=http_client), read_timeout_seconds=TOOL_TIMEOUT) as map_client,
            AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY") or None,
                           auth_token=None if base_url else os.getenv("ANTHROPIC_AUTH_TOKEN") or None,
                           base_url=base_url,timeout=60.0,max_retries=0) as model_client,
        ):
            tools = AmapTools(map_client,api_key=api_key,http_client=http_client,secrets=tuple(
                os.getenv(name, "") for name in ("ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN")))
            await tools.discover()
            yield Runtime(model_client,tools,os.environ["MODEL_ID"])
    except Exception:
        raise RuntimeError(SERVICE_MESSAGE) from None


class Submission(BaseModel):
    input: str = Field(min_length=1)
    session_id: str | None = None
    submission_id: str | None = Field(default=None, min_length=1, max_length=128)


def create_app(data_dir: str | Path, *, resources=configured_resources, static_dir: str | Path | None=None) -> FastAPI:
    directory = Path(data_dir)
    task: asyncio.Task | None = None
    active: dict[str, str] | None = None
    accepting = False
    acceptance_lock = asyncio.Lock()
    stop_requested = asyncio.Event()
    store: Store
    runtime: Runtime

    @asynccontextmanager
    async def lifespan(app):
        nonlocal store,runtime,accepting
        directory.mkdir(parents=True,exist_ok=True)
        with (directory / "travel.service.lock").open("a") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("同一数据目录已有旅行服务运行。") from None
            store = Store(directory)
            try:
                async with resources() as runtime:
                    stop_requested.clear()
                    accepting = True
                    try:
                        yield
                    finally:
                        accepting = False
                        stop_requested.set()
                        if task:
                            await task
            finally:
                store.close()
                fcntl.flock(lock,fcntl.LOCK_UN)

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_submission(_request, _error):
        # 校验错误中的原输入可能含凭据，不能直接交给浏览器或日志。
        return JSONResponse(status_code=422,content={"detail":{"code":"INVALID_INPUT","message":"输入或提交身份格式不正确，请检查后重试。"}})

    async def execute(identity, text):
        nonlocal active
        messages: list[MessageParam] = [{"role":"user","content":text}]
        outcome: dict[str, Any] = {}
        started = asyncio.get_running_loop().time()
        async def observe(kind, data):
            if kind == "turn.finished":
                outcome.update(data)
        try:
            answer = await agent_loop(messages,runtime.client,runtime.tools,runtime.model,observer=observe,stop_requested=stop_requested,stop_reason="service_shutdown")
        except Exception:
            answer = SERVICE_MESSAGE
            outcome.update(status="failed",reason="execution_error",answer_source="application",tool_error_count=0)
        try:
            answer = runtime.tools.redact(answer)
            messages = json.loads(runtime.tools.redact(json.dumps(messages,ensure_ascii=False)))
            store.finish(identity["session_id"],identity["turn_id"],answer,messages,outcome,
                         max(0,(asyncio.get_running_loop().time()-started)*1000))
        except Exception:
            # 保存失败时停止接受输入，保留最后提交事实，不能伪报终局已保存。
            nonlocal accepting
            accepting = False
        finally:
            active = None

    @app.post("/api/turns",status_code=202)
    async def submit(body: Submission):
        nonlocal task,active,accepting
        async with acceptance_lock:
            fingerprint = hashlib.sha256(json.dumps([body.input, body.session_id],ensure_ascii=False).encode()).hexdigest()
            if body.submission_id is not None:
                saved = store.submission(body.submission_id)
                if saved:
                    if saved["fingerprint"] != fingerprint:
                        raise HTTPException(409, {"code":"SUBMISSION_CONFLICT","message":"提交标识已用于其他输入或会话，请作为新提问发送。"})
                    if saved["session_id"] is None:
                        raise HTTPException(410, {"code":"SUBMISSION_DELETED","message":"这次提交的会话已删除，不会再次执行。"})
                    return {"schema_version":SCHEMA_VERSION,"submission_id":body.submission_id,"session_id":saved["session_id"],"turn_id":saved["turn_id"]}
            if not accepting:
                raise HTTPException(503,{"code":"SERVICE_UNAVAILABLE","message":"服务无法保存或正在退出，请检查后重启。"})
            if active:
                raise HTTPException(409,{"code":"BUSY","message":"已有一轮正在执行，请等待结束。"})
            if not body.input.strip():
                raise HTTPException(422,{"code":"INVALID_INPUT","message":"请输入旅行需求。"})
            text = runtime.tools.redact(body.input)
            try:
                identity = store.accept(text,submission_id=body.submission_id,fingerprint=fingerprint)
            except Exception:
                accepting = False
                raise HTTPException(503,{"code":"STORAGE_FAILURE","message":"输入未保存，查询未启动。"}) from None
            active = identity
            task = asyncio.create_task(execute(identity,text))
            return identity

    @app.get("/api/submissions/{submission_id}")
    async def submission_result(submission_id: str):
        saved = store.submission(submission_id)
        if saved is None:
            raise HTTPException(404, {"code":"SUBMISSION_NOT_FOUND","message":"服务没有接受这次提交，可安全重试同一提交。"})
        if saved["session_id"] is None:
            raise HTTPException(410, {"code":"SUBMISSION_DELETED","message":"这次提交的会话已删除，不会再次执行。"})
        return {"schema_version":SCHEMA_VERSION,"submission_id":submission_id,"session_id":saved["session_id"],"turn_id":saved["turn_id"]}

    @app.get("/api/state")
    async def state():
        return {"schema_version":SCHEMA_VERSION,"active_turn_id":active["turn_id"] if active else None,
                "active_session_id":active["session_id"] if active else None,"accepting":accepting,
                "stopping":bool(active and stop_requested.is_set()),
                "map_paused":runtime.tools.failure is not None,
                "map_pause_reason":runtime.tools.failure,
                "service_status":"available" if accepting else "unavailable"}

    @app.get("/api/sessions")
    async def sessions():
        return {"schema_version":SCHEMA_VERSION,"sessions":store.sessions()}

    @app.get("/api/sessions/{session_id}")
    async def snapshot(session_id: str):
        result = store.snapshot(session_id)
        if result is None:
            raise HTTPException(404,"会话不存在。")
        return result

    dist = Path(static_dir) if static_dir else Path(__file__).parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/",StaticFiles(directory=dist,html=True),name="page")
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本机旅行助手页面")
    parser.add_argument("--data-dir",default=".travel-data",help="SQLite 数据目录")
    parser.add_argument("--port",type=int,default=8000,help="本机端口")
    args = parser.parse_args()
    if not (Path(__file__).parent / "frontend" / "dist" / "index.html").is_file():
        print("请先运行 npm ci --prefix frontend 和 npm run build --prefix frontend 构建页面。")
        return 1
    load_dotenv(Path(__file__).with_name(".env"),override=True)
    for name in ("mcp","client","httpx2","httpcore2","anthropic"):
        logging.getLogger(name).setLevel(logging.CRITICAL+1)
    try:
        uvicorn.run(create_app(args.data_dir),host="127.0.0.1",port=args.port,workers=1,access_log=False)
    except Exception:
        print(SERVICE_MESSAGE)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
