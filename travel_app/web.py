"""一个本机服务提供旅行对话页面和同源接口。"""

import argparse
import asyncio
import fcntl
import json
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import uvicorn
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse


from fastapi.staticfiles import StaticFiles
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field

from travel_app.agent import SERVICE_MESSAGE
from travel_app.model_context import ContextConfigurationError, ContextPolicy
from travel_app.amap_http import AmapHTTPClient
from travel_app.amap_mcp import AmapTools
from travel_app.travel_tools import connected_travel_tools
from travel_app.limits import TOOL_TIMEOUT
from travel_app.local_records import SCHEMA_VERSION, StartupError, LocalRecords
from travel_app.turn_runtime import Runtime, RunRejected, TurnRuntime


@asynccontextmanager
async def configured_resources():
    ContextPolicy.from_env()
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
            async with connected_travel_tools(tools) as travel_tools:
                yield Runtime(model_client,travel_tools,os.environ["MODEL_ID"])
    except Exception:
        raise RuntimeError(SERVICE_MESSAGE) from None


class Submission(BaseModel):
    input: str = Field(min_length=1)
    session_id: str | None = None
    submission_id: str | None = Field(default=None, min_length=1, max_length=128)



class EditPreference(BaseModel):
    version: int = Field(gt=0, strict=True)
    content: str = Field(min_length=1, max_length=2000)


class RenameSession(BaseModel):
    title: str = Field(min_length=1, max_length=120)

def create_app(data_dir: str | Path, *, resources=configured_resources, static_dir: str | Path | None=None, request_shutdown: Callable[[], None] | None=None, clock: Callable[[], datetime] | None=None,
               preference_clock=time.time, preference_wait=asyncio.sleep, preference_timeout=60.0) -> FastAPI:
    directory = Path(data_dir)
    store: LocalRecords
    runtime: Runtime
    running: TurnRuntime

    @asynccontextmanager
    async def lifespan(app):
        nonlocal store,runtime,running
        directory.mkdir(parents=True,exist_ok=True)
        with (directory / "travel.service.lock").open("a") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StartupError("同一数据目录已有旅行服务运行。") from None
            store = LocalRecords(directory)
            try:
                store.recover()
                store.preferences.recover(preference_clock())
                async with resources() as runtime:
                    running = TurnRuntime(store, runtime, request_shutdown=request_shutdown, clock=clock,
                                          preference_clock=preference_clock, preference_wait=preference_wait,
                                          preference_timeout=preference_timeout)
                    running.start()
                    try:
                        yield
                    finally:
                        await running.shutdown()
            finally:
                store.close()
                fcntl.flock(lock,fcntl.LOCK_UN)

    @asynccontextmanager
    async def safe_lifespan(app):
        try:
            async with lifespan(app):
                yield
        except Exception as error:
            app.state.startup_error = str(error) if isinstance(error,(StartupError,ContextConfigurationError)) else "服务无法启动，请检查配置或存储后重启。"
            raise RuntimeError(app.state.startup_error) from None

    app = FastAPI(lifespan=safe_lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_submission(_request, _error):
        # 校验错误中的原输入可能含凭据，不能直接交给浏览器或日志。
        return JSONResponse(status_code=422,content={"detail":{"code":"INVALID_INPUT","message":"输入或提交身份格式不正确，请检查后重试。"}})

    @app.exception_handler(RunRejected)
    async def run_rejected(_request, error: RunRejected):
        return JSONResponse(status_code=error.status_code, content={"detail":error.detail})

    @app.exception_handler(sqlite3.Error)
    async def storage_exception(_request, _error):
        running.storage_failed("数据无法可靠读取或保存。")
        return JSONResponse(status_code=503,content={"detail":{"code":"STORAGE_FAILURE","message":"存储故障，数据暂时无法核对，请检查后重启。"}})

    @app.get("/api/preferences")
    async def preferences():
        return store.preferences.snapshot()

    def preference_result(result):
        if result == 'missing':
            raise HTTPException(404, detail={"message": "该偏好已删除，请刷新偏好列表。"})
        if result == 'conflict':
            raise HTTPException(409, detail={"message": "该偏好已更新，请刷新后重新修改。"})
        return store.preferences.snapshot()

    @app.patch("/api/preferences/{preference_id}")
    async def edit_preference(preference_id: str, edit: EditPreference):
        if not edit.content.strip():
            raise HTTPException(422, detail={"message": "偏好内容不能为空。"})
        return preference_result(store.preferences.edit(preference_id, edit.version, runtime.tools.redact(edit.content.strip()), preference_clock()))

    @app.delete("/api/preferences/{preference_id}")
    async def delete_preference(preference_id: str, version: int = Query(gt=0)):
        return preference_result(store.preferences.delete(preference_id, version))

    @app.post("/api/turns",status_code=202)
    async def submit(body: Submission):
        return await running.submit(body.input, body.session_id, body.submission_id)

    @app.post("/api/turns/{turn_id}/stop")
    async def stop(turn_id: str):
        return running.stop(turn_id)

    @app.get("/api/submissions/{submission_id}")
    async def submission_result(submission_id: str):
        return running.submission_result(submission_id)

    @app.get("/api/state")
    async def state():
        return running.snapshot()

    @app.get("/api/sessions")
    async def sessions(limit: int = Query(50,ge=1,le=100), cursor: str | None = None):
        try:
            return store.sessions(limit,cursor)
        except (ValueError,TypeError):
            raise HTTPException(422,"历史分页游标无效，请重新加载。") from None

    @app.get("/api/sessions/{session_id}")
    async def snapshot(session_id: str, limit: int = Query(50,ge=1,le=100), before: int | None = Query(None,ge=1), summary: bool = False, include_messages: bool = True):
        result = store.snapshot(session_id,limit,before,summary,include_messages)
        if result is None:
            raise HTTPException(404,{"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        return result

    @app.get("/api/sessions/{session_id}/search")
    async def search(session_id: str, q: str = Query(min_length=1), scope: str = "full"):
        if scope not in ("full","summary"):
            raise HTTPException(422,"搜索范围无效。")
        result = store.search(session_id,runtime.tools.redact(q),scope)
        if result is None:
            raise HTTPException(404,"会话不存在。")
        return result

    @app.get("/api/snapshot")
    async def global_snapshot():
        return {**await state(), **store.stream_position()}

    @app.get("/api/payloads/{payload_id}")
    async def payload(payload_id: str, offset: int | None = Query(None,ge=0), limit: int = Query(8192,ge=1,le=65536)):
        result = store.payload(payload_id,offset,limit)
        if result is None:
            raise HTTPException(404,"详情不存在。")
        return result

    @app.get("/api/events")
    async def events(request: Request, after: int | None=None, stream_id: str | None=None):
        try:
            # EventSource 自动续接仍保留最初 URL；已处理身份优先于初始 after。
            cursor = int(request.headers["Last-Event-ID"]) if "Last-Event-ID" in request.headers else after or 0
        except ValueError:
            raise HTTPException(409,{"code":"SNAPSHOT_REQUIRED","message":"事件游标无法续接，请重新读取快照。"}) from None
        if cursor < 0 or cursor > store.cursor() or (stream_id is not None and stream_id != store.stream_id):
            raise HTTPException(409,{"code":"SNAPSHOT_REQUIRED","message":"事件游标无法续接或数据目录已更换，请重新读取快照。"})
        async def stream():
            nonlocal cursor
            last_state = None
            while True:
                running.changed.clear()
                read_failed = False
                try:
                    batch = store.events_after(cursor)
                except sqlite3.Error:
                    running.storage_failed("执行通知暂时无法读取；仅保留最后成功提交的事实。")
                    read_failed = True
                    batch = []
                for event in batch:
                    # 等待网络发送时其他页面可能删除；不发送已级联删除的预读摘要。
                    try:
                        if not store.event_exists(event["cursor"]):
                            cursor = event["cursor"]
                            continue
                    except sqlite3.Error:
                        running.storage_failed("执行通知暂时无法读取；仅保留最后成功提交的事实。")
                        read_failed = True
                        break
                    cursor = event["cursor"]
                    yield f"id: {cursor}\nevent: trace\ndata: {json.dumps({'schema_version':SCHEMA_VERSION,**event},ensure_ascii=False)}\n\n"
                current_state = await state()
                if current_state != last_state:
                    last_state = current_state
                    notice = {"schema_version":SCHEMA_VERSION,"kind":"service.state","transient":True,"state":current_state}
                    # 实时运行状态不冒充已提交轨迹，不能推进持久化 cursor。
                    yield f"event: service.state\ndata: {json.dumps(notice,ensure_ascii=False)}\n\n"
                # yield 期间可能又有提交；先重新补齐，避免另一个订阅清空唤醒后漏等。
                if read_failed:
                    await asyncio.sleep(1)
                    continue
                try:
                    pending = store.cursor()>cursor
                except sqlite3.Error:
                    running.storage_failed("执行通知暂时无法读取；仅保留最后成功提交的事实。")
                    await asyncio.sleep(1)
                    continue
                if batch or pending or running.changed.is_set() or await state()!=last_state:
                    continue

                try:
                    await asyncio.wait_for(running.changed.wait(),15)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        return StreamingResponse(stream(),media_type="text/event-stream",headers={"Cache-Control":"no-cache"})
    @app.patch("/api/sessions/{session_id}")
    async def rename(session_id: str, body: RenameSession):
        title = runtime.tools.redact(body.title).strip()
        if not title:
            raise HTTPException(422,"请输入会话标题。")
        try:
            renamed = store.rename(session_id,title)
        except Exception:
            running.storage_failed("标题未保存。")
            raise HTTPException(503,{"code":"STORAGE_FAILURE","message":"存储故障，标题未保存，请检查后重启。"}) from None
        if not renamed:
            raise HTTPException(404,{"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        return {"schema_version":SCHEMA_VERSION,"session_id":session_id,"title":title}

    @app.delete("/api/sessions/{session_id}")
    async def delete(session_id: str):
        try:
            deleted = store.delete(session_id)
        except ValueError:
            raise HTTPException(409,{"code":"BUSY","message":"会话正在执行或停止，请等待结束后删除。"}) from None
        except Exception:
            running.storage_failed("会话删除未保存。")
            raise HTTPException(503,{"code":"STORAGE_FAILURE","message":"存储故障，会话未删除，请检查后重启。"}) from None
        if not deleted:
            raise HTTPException(404,{"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        running.changed.set()
        return {"schema_version":SCHEMA_VERSION,"deleted_session_id":session_id}

    dist = Path(static_dir) if static_dir else Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/",StaticFiles(directory=dist,html=True),name="page")
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本机旅行助手页面")
    parser.add_argument("--data-dir",default=".travel-data",help="SQLite 数据目录")
    parser.add_argument("--port",type=int,default=8000,help="本机端口")
    args = parser.parse_args()
    if not (Path(__file__).resolve().parents[1] / "frontend" / "dist" / "index.html").is_file():
        print("请先运行 npm ci --prefix frontend 和 npm run build --prefix frontend 构建页面。")
        return 1
    load_dotenv(Path(__file__).resolve().parents[1] / ".env",override=True)
    for name in ("mcp","client","httpx2","httpcore2","anthropic"):
        logging.getLogger(name).setLevel(logging.CRITICAL+1)
    try:
        connection_failed = False
        def request_shutdown():
            nonlocal connection_failed
            connection_failed = True
            server.should_exit = True
        app = create_app(args.data_dir,request_shutdown=request_shutdown)
        server = uvicorn.Server(uvicorn.Config(app,
            host="127.0.0.1",port=args.port,workers=1,access_log=False,log_level="critical",timeout_graceful_shutdown=1))
        try:
            server.run()
        except KeyboardInterrupt:
            # Uvicorn 已完成 lifespan 释放，再传播 SIGINT；正常中断不输出栈。
            return 0
        except SystemExit:
            print(getattr(app.state,"startup_error","服务无法启动，请检查配置或存储后重启。"))
            return 1
        if not server.started:
            print(getattr(app.state,"startup_error","服务无法启动，请检查配置或存储后重启。"))
            return 1
        if connection_failed:
            return 1
    except Exception:
        print(SERVICE_MESSAGE)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
