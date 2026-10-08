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
from typing import Any, Callable
from urllib.parse import urlencode

import uvicorn
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse


from fastapi.staticfiles import StaticFiles
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field

from agent import SERVICE_MESSAGE, agent_loop
from amap_http import AmapHTTPClient
from amap_mcp import AmapTools
from limits import TOOL_TIMEOUT
from store import SCHEMA_VERSION, StartupError, Store


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


class TraceStorageError(RuntimeError):
    """观察事实未提交；不改写成模型／地图失败或已保存终局。"""


class Submission(BaseModel):
    input: str = Field(min_length=1)
    session_id: str | None = None
    submission_id: str | None = Field(default=None, min_length=1, max_length=128)



class RenameSession(BaseModel):
    title: str = Field(min_length=1, max_length=120)

def create_app(data_dir: str | Path, *, resources=configured_resources, static_dir: str | Path | None=None, request_shutdown: Callable[[], None] | None=None) -> FastAPI:
    directory = Path(data_dir)
    task: asyncio.Task | None = None
    active: dict[str, str] | None = None
    accepting = False
    acceptance_lock = asyncio.Lock()
    storage_error: str | None = None

    stop_requested = asyncio.Event()
    changed = asyncio.Event()
    store: Store
    runtime: Runtime

    @asynccontextmanager
    async def lifespan(app):
        nonlocal store,runtime,accepting
        try:
            directory.mkdir(parents=True,exist_ok=True)
        except Exception:
            raise RuntimeError("数据目录无法使用，请检查存储后重启。") from None
        with (directory / "travel.service.lock").open("a") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StartupError("同一数据目录已有旅行服务运行。") from None
            store = Store(directory)
            try:
                store.recover()
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

    @asynccontextmanager
    async def safe_lifespan(app):
        try:
            async with lifespan(app):
                yield
        except Exception as error:
            app.state.startup_error = str(error) if isinstance(error,StartupError) else "服务无法启动，请检查配置或存储后重启。"
            raise RuntimeError(app.state.startup_error) from None

    app = FastAPI(lifespan=safe_lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_submission(_request, _error):
        # 校验错误中的原输入可能含凭据，不能直接交给浏览器或日志。
        return JSONResponse(status_code=422,content={"detail":{"code":"INVALID_INPUT","message":"输入或提交身份格式不正确，请检查后重试。"}})

    async def execute(identity, text):
        nonlocal active,accepting,storage_error
        messages: list[MessageParam] = store.context(identity["session_id"]) + [{"role":"user","content":text}]
        outcome: dict[str, Any] = {}
        started = asyncio.get_running_loop().time()
        async def observe(kind, data):
            if kind == "turn.finished":
                outcome.update(data)
            elif kind.startswith(("request.","tool.")):
                safe = json.loads(runtime.tools.redact(json.dumps(data,ensure_ascii=False)))
                try:
                    if kind.startswith("request."):
                        store.observe_request(identity["session_id"],identity["turn_id"],kind,safe)
                    else:
                        store.observe_tool(identity["session_id"],identity["turn_id"],kind,safe)
                except Exception:
                    raise TraceStorageError("请求事实未保存，已停止后续查询；请检查存储后重启。") from None
                changed.set()
        try:
            answer = await agent_loop(messages,runtime.client,runtime.tools,runtime.model,observer=observe,stop_requested=stop_requested,stop_reason="service_shutdown")
        except TraceStorageError as error:
            accepting = False
            storage_error = str(error)
            active = None
            changed.set()
            if runtime.tools.failure == "connection" and request_shutdown:
                request_shutdown()
            return
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
            accepting = False
            storage_error = "轮次结果未保存，请检查存储后重启。"
        finally:
            active = None
            if runtime.tools.failure == "connection":
                accepting = False
                if request_shutdown:
                    request_shutdown()
            changed.set()

    @app.post("/api/turns",status_code=202)
    async def submit(body: Submission):
        nonlocal task,active,accepting,storage_error
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
                identity = store.accept(text,body.session_id,submission_id=body.submission_id,fingerprint=fingerprint)
            except KeyError:
                raise HTTPException(404,{"code":"SESSION_NOT_FOUND","message":"会话不存在。"}) from None
            except Exception:
                accepting = False
                storage_error = "输入未保存，查询未启动，请检查存储后重启。"
                changed.set()
                raise HTTPException(503,{"code":"STORAGE_FAILURE","message":"输入未保存，查询未启动。"}) from None
            active = identity
            changed.set()
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
                "service_status":"available" if accepting else "unavailable","storage_error":storage_error}


    @app.get("/api/sessions")
    async def sessions(limit: int = Query(50,ge=1,le=100), cursor: str | None = None):
        try:
            return store.sessions(limit,cursor)
        except (ValueError,TypeError):
            raise HTTPException(422,"历史分页游标无效，请重新加载。") from None

    @app.get("/api/sessions/{session_id}")
    async def snapshot(session_id: str, limit: int = Query(50,ge=1,le=100), before: int | None = Query(None,ge=1)):
        result = store.snapshot(session_id,limit,before)
        if result is None:
            raise HTTPException(404,{"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        return result

    @app.get("/api/snapshot")
    async def global_snapshot():
        with store.db:
            return {**await state(),"cursor":store.cursor(),"stream_id":store.stream_id}

    @app.get("/api/payloads/{payload_id}")
    async def payload(payload_id: str):
        result = store.payload(payload_id)
        if result is None:
            raise HTTPException(404,"详情不存在。")
        return result

    @app.get("/api/events")
    async def events(request: Request, after: int | None=None, stream_id: str | None=None):
        try:
            cursor = after if after is not None else int(request.headers.get("Last-Event-ID", "0"))
        except ValueError:
            raise HTTPException(409,"事件游标无法续接，请重新读取快照。") from None
        if cursor < 0 or cursor > store.cursor() or (stream_id is not None and stream_id != store.stream_id):
            raise HTTPException(409,"事件游标无法续接，请重新读取快照。")
        async def stream():
            nonlocal cursor
            last_state = None
            while True:
                changed.clear()
                batch = store.events_after(cursor)
                for event in batch:
                    cursor = event["cursor"]
                    yield f"id: {cursor}\nevent: trace\ndata: {json.dumps({'schema_version':SCHEMA_VERSION,**event},ensure_ascii=False)}\n\n"
                current_state = await state()
                if current_state != last_state:
                    last_state = current_state
                    notice = {"schema_version":SCHEMA_VERSION,"kind":"service.state","transient":True,"state":current_state}
                    # 实时运行状态不冒充已提交轨迹，不能推进持久化 cursor。
                    yield f"event: service.state\ndata: {json.dumps(notice,ensure_ascii=False)}\n\n"
                # yield 期间可能又有提交；先重新补齐，避免另一个订阅清空唤醒后漏等。
                if batch:
                    continue

                try:
                    await asyncio.wait_for(changed.wait(),15)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        return StreamingResponse(stream(),media_type="text/event-stream",headers={"Cache-Control":"no-cache"})
    @app.patch("/api/sessions/{session_id}")
    async def rename(session_id: str, body: RenameSession):
        title = runtime.tools.redact(body.title).strip()
        if not title:
            raise HTTPException(422,"请输入会话标题。")
        try:
            changed = store.rename(session_id,title)
        except Exception:
            raise HTTPException(503,{"code":"STORAGE_FAILURE","message":"标题未保存，请重试。"}) from None
        if not changed:
            raise HTTPException(404,{"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        return {"schema_version":SCHEMA_VERSION,"session_id":session_id,"title":title}

    @app.delete("/api/sessions/{session_id}")
    async def delete(session_id: str):
        try:
            deleted = store.delete(session_id)
        except ValueError:
            raise HTTPException(409,{"code":"BUSY","message":"会话正在执行或停止，请等待结束后删除。"}) from None
        except Exception:
            raise HTTPException(503,{"code":"STORAGE_FAILURE","message":"会话未删除，请重试。"}) from None
        if not deleted:
            raise HTTPException(404,{"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        return {"schema_version":SCHEMA_VERSION,"deleted_session_id":session_id}

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
