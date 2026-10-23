"""集中 Web 对话轮次的接受、停止、保存故障与后台生命周期。"""

import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from anthropic import AsyncAnthropic

from travel_app.planning import PlanningTools, plan
from travel_app.local_records import LocalRecords, RecordAbort, SCHEMA_VERSION
from travel_app.preferences import Preferences


@dataclass
class Runtime:
    client: AsyncAnthropic
    tools: PlanningTools
    model: str


class RunRejected(Exception):
    def __init__(self, status_code: int, detail: dict[str, str] | str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail['message'] if isinstance(detail, dict) else detail)


class TurnRuntime:
    def __init__(self, store: LocalRecords, runtime: Runtime, *, request_shutdown: Callable[[], None] | None = None,
                 clock: Callable[[], datetime] | None = None, preference_clock=time.time,
                 preference_wait=asyncio.sleep, preference_timeout=60.0):
        self.store = store
        self.runtime = runtime
        self.request_shutdown = request_shutdown
        self.clock = clock
        self.preference_clock = preference_clock
        self._task: asyncio.Task | None = None
        self._active: dict[str, str] | None = None
        self._accepting = False
        self._acceptance_lock = asyncio.Lock()
        self._storage_error: str | None = None
        self._unsaved_fact: dict[str, str] | None = None
        self._stop_requested = asyncio.Event()
        self._stop_reason = 'user_stop'
        self.changed = asyncio.Event()
        self.preferences = Preferences(store, runtime.client, runtime.model, redact=runtime.tools.redact,
                                       clock=preference_clock, wait=preference_wait, timeout=preference_timeout,
                                       storage_failed=self.storage_failed, available=lambda: self._accepting)

    def start(self):
        self._stop_requested.clear()
        self._accepting = True
        self.preferences.start()

    async def shutdown(self):
        self._accepting = False
        if not self._stop_requested.is_set():
            self._stop_reason = 'service_shutdown'
        self._stop_requested.set()
        self.changed.set()
        await self.preferences.shutdown()
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

    async def _run_turn(self, identity, text):
        try:
            await self._execute(identity, text)
        finally:
            # 已发起调用与保存尝试真正收束后才释放全局名额。
            self._active = None
            self.changed.set()

    async def _execute(self, identity, text):
        started = asyncio.get_running_loop().time()
        async def observe(kind, data):
            if kind.startswith(("request.","tool.","context.")):
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
                    raise RecordAbort(message) from None
                self.changed.set()
        def history_records():
            try:
                return self.store.history_records(identity["session_id"])
            except Exception:
                message = "历史原文无法可靠读取，已停止后续查询；仅保留最后成功提交的事实。"
                self.storage_failed(message, kind="tool.history_read", turn_id=identity["turn_id"])
                raise RecordAbort(message) from None
        try:
            try:
                seed = self.store.planning_seed(identity["session_id"])
                background = self.preferences.background()
            except Exception:
                self.storage_failed("完整上下文无法读取，查询未启动。",kind="turn.context",turn_id=identity["turn_id"])
                raise RecordAbort from None
            result = await plan(text, self.runtime.client, self.runtime.tools, self.runtime.model,
                seed=seed, observer=observe, stop_requested=self._stop_requested,
                stop_reason=lambda: self._stop_reason, preference_background=background,
                clock=self.clock, history_records=history_records)
        except RecordAbort as error:
            if self._storage_error is None:
                kind = error.kind or "turn.context"
                message = ("轮次保存材料无法生成；仅保留最后成功提交的事实。" if kind == "turn.finished"
                           else "完整上下文无法恢复，查询未启动。")
                self.storage_failed(message, kind=kind, turn_id=identity["turn_id"])
            return
        try:
            self.store.finish(identity["session_id"], identity["turn_id"], result.answer,
                              result.material, result.fields(),
                              max(0, (asyncio.get_running_loop().time()-started)*1000),
                              preference_now=self.preference_clock())
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
            self.preferences.stop(turn_id)
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
                "map_paused":self.runtime.tools.capabilities.map_pause_reason is not None,
                "map_pause_reason":self.runtime.tools.capabilities.map_pause_reason,
                "service_status":"available" if self._accepting else "unavailable","storage_error":self._storage_error,
                "unsaved_fact":self._unsaved_fact}

    def _read(self, operation, *args):
        try:
            return operation(*args)
        except sqlite3.Error:
            self.storage_failed("数据无法可靠读取或保存。")
            raise RunRejected(503, {"code":"STORAGE_FAILURE","message":"存储故障，数据暂时无法核对，请检查后重启。"}) from None

    def history(self, limit: int = 50, cursor: str | None = None):
        try:
            return self._read(self.store.sessions, limit, cursor)
        except (ValueError, TypeError):
            raise RunRejected(422, "历史分页游标无效，请重新加载。") from None

    def history_page(self, session_id: str, limit: int = 50, before: int | None = None,
                     summary: bool = False, include_messages: bool = True):
        result = self._read(self.store.snapshot, session_id, limit, before, summary, include_messages)
        if result is None:
            raise RunRejected(404, {"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        return result

    def search(self, session_id: str, query: str, scope: str = "full"):
        if scope not in ("full", "summary"):
            raise RunRejected(422, "搜索范围无效。")
        result = self._read(self.store.search, session_id, self.runtime.tools.redact(query), scope)
        if result is None:
            raise RunRejected(404, "会话不存在。")
        return result

    def payload(self, payload_id: str, offset: int | None = None, limit: int = 8192):
        result = self._read(self.store.payload, payload_id, offset, limit)
        if result is None:
            raise RunRejected(404, "详情不存在。")
        return result

    def service_snapshot(self):
        return {**self.snapshot(), **self._read(self.store.stream_position)}

    def rename(self, session_id: str, title: str):
        title = self.runtime.tools.redact(title).strip()
        if not title:
            raise RunRejected(422, "请输入会话标题。")
        try:
            renamed = self.store.rename(session_id, title)
        except Exception:
            self.storage_failed("标题未保存。")
            raise RunRejected(503, {"code":"STORAGE_FAILURE","message":"存储故障，标题未保存，请检查后重启。"}) from None
        if not renamed:
            raise RunRejected(404, {"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        return {"schema_version":SCHEMA_VERSION,"session_id":session_id,"title":title}

    def delete(self, session_id: str):
        try:
            deleted = self.store.delete(session_id)
        except ValueError:
            raise RunRejected(409, {"code":"BUSY","message":"会话正在执行或停止，请等待结束后删除。"}) from None
        except Exception:
            self.storage_failed("会话删除未保存。")
            raise RunRejected(503, {"code":"STORAGE_FAILURE","message":"存储故障，会话未删除，请检查后重启。"}) from None
        if not deleted:
            raise RunRejected(404, {"code":"SESSION_NOT_FOUND","message":"会话不存在。"})
        self.changed.set()
        return {"schema_version":SCHEMA_VERSION,"deleted_session_id":session_id}

    def notifications(self, after: int | None = None, stream_id: str | None = None, last_event_id: str | None = None):
        try:
            # EventSource 自动续接仍保留最初 URL；已处理身份优先于初始 after。
            cursor = int(last_event_id) if last_event_id is not None else after or 0
        except ValueError:
            raise RunRejected(409,{"code":"SNAPSHOT_REQUIRED","message":"事件游标无法续接，请重新读取快照。"}) from None
        position = self._read(self.store.stream_position)
        if cursor < 0 or cursor > position["cursor"] or (stream_id is not None and stream_id != position["stream_id"]):
            raise RunRejected(409,{"code":"SNAPSHOT_REQUIRED","message":"事件游标无法续接或数据目录已更换，请重新读取快照。"})
        async def stream():
            nonlocal cursor
            last_state = None
            while True:
                self.changed.clear()
                read_failed = False
                try:
                    batch = self.store.events_after(cursor)
                except sqlite3.Error:
                    self.storage_failed("执行通知暂时无法读取；仅保留最后成功提交的事实。")
                    read_failed = True
                    batch = []
                for event in batch:
                    # 等待网络发送时其他页面可能删除；不发送已级联删除的预读摘要。
                    try:
                        if not self.store.event_exists(event["cursor"]):
                            cursor = event["cursor"]
                            continue
                    except sqlite3.Error:
                        self.storage_failed("执行通知暂时无法读取；仅保留最后成功提交的事实。")
                        read_failed = True
                        break
                    cursor = event["cursor"]
                    yield f"id: {cursor}\nevent: trace\ndata: {json.dumps({'schema_version':SCHEMA_VERSION,**event},ensure_ascii=False)}\n\n"
                current_state = self.snapshot()
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
                    pending = self.store.cursor()>cursor
                except sqlite3.Error:
                    self.storage_failed("执行通知暂时无法读取；仅保留最后成功提交的事实。")
                    await asyncio.sleep(1)
                    continue
                if batch or pending or self.changed.is_set() or self.snapshot()!=last_state:
                    continue

                try:
                    await asyncio.wait_for(self.changed.wait(),15)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        return stream()
