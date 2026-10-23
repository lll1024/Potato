"""一个本机服务提供旅行对话页面和同源接口。"""

import argparse
import asyncio
import fcntl
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse


from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from travel_app.planning import SERVICE_MESSAGE, ContextConfigurationError, configured_planning, validate_configuration
from travel_app.local_records import SCHEMA_VERSION, StartupError, LocalRecords
from .runtime import Runtime, RunRejected, TurnRuntime


@asynccontextmanager
async def configured_resources():
    validate_configuration()
    if not os.getenv("AMAP_MAPS_API_KEY", "").strip() or not os.getenv("MODEL_ID"):
        raise RuntimeError("请配置 AMAP_MAPS_API_KEY 和 MODEL_ID。")
    try:
        async with configured_planning() as resources:
            yield Runtime(resources.client, resources.tools, resources.model)
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
        return running.preferences.snapshot()

    def preference_result(result):
        if result == 'missing':
            raise HTTPException(404, detail={"message": "该偏好已删除，请刷新偏好列表。"})
        if result == 'conflict':
            raise HTTPException(409, detail={"message": "该偏好已更新，请刷新后重新修改。"})
        return running.preferences.snapshot()

    @app.patch("/api/preferences/{preference_id}")
    async def edit_preference(preference_id: str, edit: EditPreference):
        if not edit.content.strip():
            raise HTTPException(422, detail={"message": "偏好内容不能为空。"})
        return preference_result(running.preferences.edit(preference_id, edit.version, edit.content))

    @app.delete("/api/preferences/{preference_id}")
    async def delete_preference(preference_id: str, version: int = Query(gt=0)):
        return preference_result(running.preferences.delete(preference_id, version))

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
        return running.history(limit, cursor)

    @app.get("/api/sessions/{session_id}")
    async def snapshot(session_id: str, limit: int = Query(50,ge=1,le=100), before: int | None = Query(None,ge=1), summary: bool = False, include_messages: bool = True):
        return running.history_page(session_id, limit, before, summary, include_messages)

    @app.get("/api/sessions/{session_id}/search")
    async def search(session_id: str, q: str = Query(min_length=1), scope: str = "full"):
        return running.search(session_id, q, scope)

    @app.get("/api/snapshot")
    async def global_snapshot():
        return running.service_snapshot()

    @app.get("/api/payloads/{payload_id}")
    async def payload(payload_id: str, offset: int | None = Query(None,ge=0), limit: int = Query(8192,ge=1,le=65536)):
        return running.payload(payload_id, offset, limit)

    @app.get("/api/events")
    async def events(request: Request, after: int | None=None, stream_id: str | None=None):
        return StreamingResponse(running.notifications(after, stream_id, request.headers.get("Last-Event-ID")),
                                 media_type="text/event-stream", headers={"Cache-Control":"no-cache"})
    @app.patch("/api/sessions/{session_id}")
    async def rename(session_id: str, body: RenameSession):
        return running.rename(session_id, body.title)

    @app.delete("/api/sessions/{session_id}")
    async def delete(session_id: str):
        return running.delete(session_id)

    dist = Path(static_dir) if static_dir else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/",StaticFiles(directory=dist,html=True),name="page")
    return app


def main(*, app_factory=create_app, load_environment=load_dotenv) -> int:
    parser = argparse.ArgumentParser(description="启动本机旅行助手页面")
    parser.add_argument("--data-dir",default=".travel-data",help="SQLite 数据目录")
    parser.add_argument("--port",type=int,default=8000,help="本机端口")
    args = parser.parse_args()
    if not (Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html").is_file():
        print("请先运行 npm ci --prefix frontend 和 npm run build --prefix frontend 构建页面。")
        return 1
    load_environment(Path(__file__).resolve().parents[2] / ".env",override=True)
    for name in ("mcp","client","httpx2","httpcore2","anthropic"):
        logging.getLogger(name).setLevel(logging.CRITICAL+1)
    try:
        connection_failed = False
        def request_shutdown():
            nonlocal connection_failed
            connection_failed = True
            server.should_exit = True
        app = app_factory(args.data_dir,request_shutdown=request_shutdown)
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
