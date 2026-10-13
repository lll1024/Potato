"""集中 Web 对话轮次的接受、停止、保存故障与后台生命周期。"""

import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from travel_app.agent import agent_loop
from travel_app.amap_mcp import AmapTools
from travel_app.model_context import ContextView
from travel_app.preferences import extract_preferences, preference_background
from travel_app.store import SCHEMA_VERSION, Store
from travel_app.travel_tools import TravelTools


@dataclass
class Runtime:
    client: AsyncAnthropic
    tools: AmapTools | TravelTools
    model: str


class TraceStorageError(RuntimeError):
    """观察事实未提交；不改写成模型／地图失败或已保存终局。"""


class RunRejected(Exception):
    def __init__(self, status_code: int, detail: dict[str, str]):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail['message'])


class TurnRuntime:
    def __init__(self, store: Store, runtime: Runtime, *, request_shutdown: Callable[[], None] | None = None,
                 clock: Callable[[], datetime] | None = None, preference_clock=time.time,
                 preference_wait=asyncio.sleep, preference_timeout=60.0):
        self.store = store
        self.runtime = runtime
        self.request_shutdown = request_shutdown
        self.clock = clock
        self.preference_clock = preference_clock
        self.preference_wait = preference_wait
        self.preference_timeout = preference_timeout
        self._task: asyncio.Task | None = None
        self._preference_task: asyncio.Task | None = None
        self._active: dict[str, str] | None = None
        self._accepting = False
        self._acceptance_lock = asyncio.Lock()
        self._storage_error: str | None = None
        self._unsaved_fact: dict[str, str] | None = None
        self._stop_requested = asyncio.Event()
        self._stop_reason = 'user_stop'
        self.changed = asyncio.Event()

    def start(self):
        self._stop_requested.clear()
        self._accepting = True
        self._preference_task = asyncio.create_task(self._process_preferences())

    async def shutdown(self):
        self._accepting = False
        if not self._stop_requested.is_set():
            self._stop_reason = 'service_shutdown'
        self._stop_requested.set()
        self.changed.set()
        if self._preference_task:
            self._preference_task.cancel()
            try:
                await self._preference_task
            except asyncio.CancelledError:
                pass
        if self._task:
            await self._task

    def storage_failed(self, message: str, *, kind: str | None = None, turn_id: str | None = None):
        previous = (self._accepting,self._storage_error,self._unsaved_fact)
        self._accepting = False
        self._storage_error = "存储故障：" + message + " 请检查存储后重启。"
        if kind is not None:
            self._unsaved_fact = {"kind":kind,"turn_id":turn_id or "","message":message}
        # 并发历史写入失败时，已进入 SDK 的调用仍等待真实返回。
        # 后续边界复用停止信号；名额由 _run_turn 在真正结束后释放。
        if not self._stop_requested.is_set():
            self._stop_reason = "storage_failure"
        self._stop_requested.set()
        if previous != (self._accepting,self._storage_error,self._unsaved_fact):
            self.changed.set()

    async def _process_preferences(self):
        while True:
            try:
                batch = self.store.preferences.eligible(self.preference_clock()) if self._accepting else None
                if batch:
                    existing = self.store.preferences.snapshot()['preferences']
                    if not self.store.preferences.start(batch, self.preference_clock()):
                        continue
                    try:
                        changes = await asyncio.wait_for(extract_preferences(self.runtime, batch, existing), self.preference_timeout)
                    except sqlite3.Error:
                        raise
                    except Exception:
                        self.store.preferences.failed(batch, self.preference_clock())
                    else:
                        self.store.preferences.commit(batch, changes, self.preference_clock())
                    continue
            except sqlite3.Error:
                self.storage_failed("偏好存储无法可靠读取或保存。")
            await self.preference_wait(30)

    async def _run_turn(self, identity, text):
        try:
            await self._execute(identity, text)
        finally:
            # 已发起调用与保存尝试真正收束后才释放全局名额。
            self._active = None
            if self.runtime.tools.failure == 'connection':
                self._accepting = False
                if self.request_shutdown:
                    self.request_shutdown()
            self.changed.set()

    async def _execute(self, identity, text):
        messages: list[MessageParam] = []
        outcome: dict[str, Any] = {}
        context_view = None
        started = asyncio.get_running_loop().time()
        async def observe(kind, data):
            if kind == "turn.finished":
                outcome.update(data)
            elif kind.startswith(("request.","tool.","context.")):
                safe = json.loads(self.runtime.tools.redact(json.dumps(data,ensure_ascii=False)))
                try:
                    if kind.startswith("request."):
                        self.store.observe_request(identity["session_id"],identity["turn_id"],kind,safe)
                    elif kind.startswith("context."):
                        self.store.observe_context(identity["session_id"],identity["turn_id"],kind,safe)
                    else:
                        self.store.observe_tool(identity["session_id"],identity["turn_id"],kind,safe)
                except Exception:
                    message = ("调用返回或错误已收到，但未保存；仅保留最后成功提交的事实。"
                               if kind in ("request.completed","request.failed","tool.completed","tool.failed")
                               else "调用开始或执行事实未保存，已停止后续查询。")
                    self.storage_failed(message,kind=kind,turn_id=identity["turn_id"])
                    raise TraceStorageError(message) from None
                self.changed.set()
        def history_records():
            try:
                return self.store.history_records(identity["session_id"])
            except Exception:
                message = "历史原文无法可靠读取，已停止后续查询；仅保留最后成功提交的事实。"
                self.storage_failed(message, kind="tool.history_read", turn_id=identity["turn_id"])
                raise TraceStorageError(message) from None
        try:
            try:
                messages = self.store.context(identity["session_id"]) + [{"role":"user","content":text}]
                state = self.store.context_state(identity["session_id"])
                context_view = ContextView.from_state(state, messages) if state else ContextView.from_history(messages)
                previous_date_context = self.store.date_context(identity["session_id"])
                previous_source_materials = self.store.source_context(identity["session_id"])
                preference_snapshot = self.store.preferences.snapshot()
                background = preference_background(preference_snapshot["preferences"], preference_snapshot["ambiguities"])
            except Exception:
                self.storage_failed("完整上下文无法读取，查询未启动。",kind="turn.context",turn_id=identity["turn_id"])
                raise TraceStorageError from None
            answer = await agent_loop(messages,self.runtime.client,self.runtime.tools,self.runtime.model,observer=observe,stop_requested=self._stop_requested,stop_reason=lambda: self._stop_reason,
                                      preference_background=background, clock=self.clock, previous_date_context=previous_date_context,
                                      previous_source_materials=previous_source_materials, context_view=context_view,
                                      history_records=history_records)
        except TraceStorageError:
            return
        except Exception:
            answer = "这次请求未能完成，旅行行程尚未完成。请查看处理记录或重新尝试。"
            outcome.update(status="failed",reason="execution_error",answer_source="application",tool_error_count=0)
        try:
            answer = self.runtime.tools.redact(answer)
            messages = json.loads(self.runtime.tools.redact(json.dumps(messages,ensure_ascii=False)))
            self.store.finish(identity["session_id"],identity["turn_id"],answer,messages,outcome,
                         max(0,(asyncio.get_running_loop().time()-started)*1000), preference_now=self.preference_clock(), context_view=context_view)
        except Exception:
            # 保存失败时停止接受输入，保留最后提交事实，不能伪报终局已保存。
            self.storage_failed("轮次结果未保存；仅保留最后成功提交的事实。",kind="turn.finished",turn_id=identity["turn_id"])

    async def submit(self, input: str, session_id: str | None, submission_id: str | None):
        async with self._acceptance_lock:
            fingerprint = hashlib.sha256(json.dumps([input, session_id],ensure_ascii=False).encode()).hexdigest()
            if submission_id is not None:
                saved = self.store.submission(submission_id)
                if saved:
                    if saved["fingerprint"] != fingerprint:
                        raise RunRejected(409, {"code":"SUBMISSION_CONFLICT","message":"提交标识已用于其他输入或会话，请作为新提问发送。"})
                    if saved["session_id"] is None:
                        raise RunRejected(410, {"code":"SUBMISSION_DELETED","message":"这次提交的会话已删除，不会再次执行。"})
                    return {"schema_version":SCHEMA_VERSION,"submission_id":submission_id,"session_id":saved["session_id"],"turn_id":saved["turn_id"]}
            if not self._accepting:
                raise RunRejected(503,{"code":"SERVICE_UNAVAILABLE","message":"服务无法保存或正在退出，请检查后重启。"})
            if self._active:
                raise RunRejected(409,{"code":"BUSY","message":"已有一轮正在执行，请等待结束。"})
            if not input.strip():
                raise RunRejected(422,{"code":"INVALID_INPUT","message":"请输入旅行需求。"})
            text = self.runtime.tools.redact(input)
            try:
                identity = self.store.accept(text,session_id,submission_id=submission_id,fingerprint=fingerprint,preference_now=self.preference_clock())
            except KeyError:
                raise RunRejected(404,{"code":"SESSION_NOT_FOUND","message":"会话不存在。"}) from None
            except Exception:
                self.storage_failed("输入未保存，查询未启动。",kind="turn.accepted")
                raise RunRejected(503,{"code":"STORAGE_FAILURE","message":"输入未保存，查询未启动。"}) from None
            self._stop_requested.clear()
            self._stop_reason = "user_stop"
            self._active = identity
            self.changed.set()
            self._task = asyncio.create_task(self._run_turn(identity,text))
            return identity

    def stop(self, turn_id: str):
        # 请求目标始终是调用方保存的轮次身份，不随页面选中会话迁移。
        saved = self.store.turn(turn_id)
        if saved is None:
            raise RunRejected(404, {"code":"TURN_NOT_FOUND","message":"轮次不存在。"})
        if self._active and self._active["turn_id"] == turn_id:
            self.store.preferences.cancel(turn_id)
            self._stop_requested.set()
            self.changed.set()
        return {"schema_version":SCHEMA_VERSION,"turn_id":turn_id,"status":saved["status"],
                "stopping":bool(self._active and self._active["turn_id"] == turn_id and self._stop_requested.is_set())}

    def submission_result(self, submission_id: str):
        saved = self.store.submission(submission_id)
        if saved is None:
            raise RunRejected(404, {"code":"SUBMISSION_NOT_FOUND","message":"服务没有接受这次提交，可安全重试同一提交。"})
        if saved["session_id"] is None:
            raise RunRejected(410, {"code":"SUBMISSION_DELETED","message":"这次提交的会话已删除，不会再次执行。"})
        return {"schema_version":SCHEMA_VERSION,"submission_id":submission_id,"session_id":saved["session_id"],"turn_id":saved["turn_id"]}


    def snapshot(self):
        return {"schema_version":SCHEMA_VERSION,"active_turn_id":self._active["turn_id"] if self._active else None,
                "active_session_id":self._active["session_id"] if self._active else None,"accepting":self._accepting,
                "stopping":bool(self._active and self._stop_requested.is_set()),
                "map_paused":self.runtime.tools.failure is not None,
                "map_pause_reason":self.runtime.tools.failure,
                "service_status":"available" if self._accepting else "unavailable","storage_error":self._storage_error,
                "unsaved_fact":self._unsaved_fact}
