"""集中准备模型上下文；压力与超限恢复共享本轮视图、计量及摘要额度。"""
import copy
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from .summary import earlier_history_cutoff, summarize_history
from .view import ContextPolicy, ContextView, TokenMeter
from .history import SessionHistory, bind_protocol_references, protocol_records
from travel_app.local_records import ContextCheckpoint, PlanningSeed, RecordAbort, TurnMaterial


class Continuation:
    """不透明的进程内延续凭证；只持有冻结快照。"""
    def __init__(self, seed: PlanningSeed):
        self._seed = seed


class ModelContext:
    def __init__(self, seed: PlanningSeed):
        try:
            self._messages = json.loads(seed.messages_json)
            if not isinstance(self._messages, list):
                raise ValueError("原始协议必须是消息列表。")
            state = json.loads(seed.context_json) if seed.context_json else None
            self._view = ContextView.from_state(state, self._messages) if state else ContextView.from_history(self._messages)
            self._date_context = json.loads(seed.date_context_json) if seed.date_context_json else None
            self._source_materials = json.loads(seed.source_materials_json)
        except Exception:
            raise RecordAbort("完整上下文无法恢复，查询未启动。", kind="turn.context") from None

    @property
    def summary_blocked(self) -> bool:
        """认证或额度故障后，摘要不能继续占用共享模型请求。"""
        return self._summary_blocked

    @classmethod
    def begin(cls, text: str, *, seed: PlanningSeed | None = None, continuation: Continuation | None = None) -> "ModelContext":
        if seed is not None and continuation is not None:
            raise ValueError("持久上下文与进程内延续凭证只能选择一个。")
        context = cls(continuation._seed if continuation else seed or PlanningSeed())
        try:
            context._append({"role": "user", "content": text})
        except Exception:
            raise RecordAbort("完整上下文无法恢复，查询未启动。", kind="turn.context") from None
        return context

    @property
    def previous_date_context(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._date_context)

    @property
    def source_materials(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._source_materials)

    @property
    def latest_input(self) -> str:
        return next((message["content"] for message in reversed(self._messages)
                     if message["role"] == "user" and isinstance(message["content"], str)), "")

    def _append(self, message):
        self._messages.append(copy.deepcopy(message))
        self._view.append(message, len(self._messages))

    def append_response(self, content: list[dict[str, Any]]) -> None:
        self._append({"role": "assistant", "content": content})

    def append_tool_results(self, results: Sequence[Mapping[str, Any]]) -> None:
        response = self._messages[-1]
        content = response.get("content")
        expected = [block["id"] for block in content if block.get("type") == "tool_use"] if (
            response["role"] == "assistant" and isinstance(content, list)) else []
        actual = [block["tool_use_id"] for block in results if block.get("type") == "tool_result"]
        if not expected or len(actual) != len(results) or sorted(actual) != sorted(expected):
            raise ValueError("完整工具结果必须与上一响应的调用身份一一配对。")
        self._append({"role": "user", "content": results})

    def append_answer(self, answer: str, *, replace_response: bool = False) -> None:
        message = {"role": "assistant", "content": answer}
        if replace_response:
            self._messages[-1] = message
            self._view.messages[-1] = copy.deepcopy(message)
        else:
            self._append(message)

    def material(self, redact: Callable[[str], str] = lambda value: value) -> TurnMaterial:
        try:
            return TurnMaterial(redact(json.dumps(self._messages, ensure_ascii=False)),
                                ContextCheckpoint.capture(json.loads(redact(json.dumps(self._view.to_state(), ensure_ascii=False)))))
        except Exception:
            raise RecordAbort("轮次保存材料无法生成；仅保留最后成功提交的事实。", kind="turn.finished") from None

    def continuation(self) -> Continuation:
        material = self.material()
        assert material.context_checkpoint is not None
        return Continuation(PlanningSeed(messages_json=material.messages_json,
            context_json=material.context_checkpoint.state_json,
            date_context_json=json.dumps(self._date_context, ensure_ascii=False)))

    def configure(self, *, policy: ContextPolicy | None, model: str, client, tools, date_context: dict[str, Any] | None,
                  next_request_ordinal, remaining_requests, observer, stop_requested, history_records=None):
        self._messages = json.loads(tools.redact(json.dumps(self._messages, ensure_ascii=False)))
        self._view.messages = json.loads(tools.redact(json.dumps(self._view.messages, ensure_ascii=False)))
        self._policy = policy
        self._meter = TokenMeter(model, usage_semantics="anthropic" if not os.getenv("ANTHROPIC_BASE_URL") else "compatible_unknown")
        self._client, self._tools = client, tools
        self._date_context, self._turn_start = copy.deepcopy(date_context), len(self._messages) - 1
        self._next_request_ordinal, self._remaining_requests = next_request_ordinal, remaining_requests
        self._observer, self._stop_requested = observer, stop_requested
        self._summary_attempts, self._summary_blocked = 0, False
        self._measurement: dict[str, Any] | None = None
        self._recovery_steps = set()
        self._history_records = history_records
        self._history = SessionHistory(self._records)

    def _records(self, partial_results=()):
        protocol = [*self._messages, {"role": "user", "content": list(partial_results)}] if partial_results else self._messages
        return bind_protocol_references(self._history_records(), protocol) if self._history_records else protocol_records(protocol)

    async def read_history(self, name, arguments, *, parameters, completed, pending, tool_use_id, observer=None):
        def fits(content):
            remaining = [{"type": "tool_result", "tool_use_id": identifier, "content": "", "is_error": True} for identifier in pending]
            return self._fits_tool_results(parameters, [*completed,
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": False}, *remaining])
        self._history.records = lambda: self._records(completed)
        return await self._history.call(name, arguments, observer=observer, fits=fits)

    async def recovery_started(self, data):
        await self._emit("context.recovery_started", {**data, "version": self._view.version})

    async def _emit(self, kind, data):
        if self._observer:
            await self._observer(kind, data)

    def _measure_view(self, parameters):
        parameters["messages"] = copy.deepcopy(self._view.messages)
        return self._meter.measure(parameters)

    def _within_budget(self, measurement):
        return self._policy is not None and measurement["estimated_tokens"] <= self._policy.input_budget

    async def _organize(self, parameters, measurement, *, step, attempt, recovery=False):
        before_tokens = measurement["estimated_tokens"]
        changed = self._view.organize_tool_results()
        measurement = self._measure_view(parameters)
        data = {"before_tokens": before_tokens, "after_tokens": measurement["estimated_tokens"],
                "changed_result_count": changed,
                "pressure_remaining": self._policy is None or measurement["estimated_tokens"] >= self._policy.pressure_threshold,
                "original_watermark": self._view.original_watermark, "version": self._view.version,
                "record_ids": self._view.record_ids.copy(), "step": step, "attempt": attempt}
        if not recovery:
            assert self._policy is not None
            data["pressure_threshold"] = self._policy.pressure_threshold
        await self._emit("context.organized", data)
        return measurement

    async def _summarize(self, parameters, *, force=False):
        if self._policy is None or self._summary_attempts >= 4 or self._summary_blocked or not self._remaining_requests():
            return
        result = await summarize_history(
            view=self._view, policy=self._policy, meter=self._meter, parameters=parameters,
            client=self._client, tools=self._tools, date_context=self._date_context,
            cutoff=earlier_history_cutoff(self._view, self._turn_start, self._policy, self._meter, force=force),
            remaining_attempts=min(4 - self._summary_attempts, self._remaining_requests()), next_request_ordinal=self._next_request_ordinal,
            observer=self._observer, stop_requested=self._stop_requested)
        self._summary_attempts += result.attempts
        self._summary_blocked = self._summary_blocked or result.terminal_failure

    async def prepare_request(self, parameters, *, step, attempt, recovering=False, final=False):
        """更新可发送的输入；返回预算阻止原因，关闭压缩时沿用原输入。"""
        if self._policy:
            measurement = self._measure_view(parameters)
        else:
            parameters["messages"] = copy.deepcopy(self._view.messages)
            measurement = None
        self._measurement = measurement
        if self._policy is None:
            return None
        assert measurement is not None
        if not final and not recovering and measurement["estimated_tokens"] >= self._policy.pressure_threshold:
            measurement = await self._organize(parameters, measurement, step=step, attempt=attempt)
            if (measurement["estimated_tokens"] >= self._policy.pressure_threshold
                    and self._summary_attempts < 4 and not self._summary_blocked):
                await self._summarize(parameters)
                measurement = self._measure_view(parameters)
        self._measurement = measurement
        allowed = self._within_budget(measurement)
        await self._emit("context.checked", {**measurement, "window": self._policy.window,
            "input_budget": self._policy.input_budget, "allowed": allowed,
            "original_watermark": self._view.original_watermark, "version": self._view.version,
            "record_ids": self._view.record_ids.copy(), "step": step, "attempt": attempt})
        return None if allowed else "context_budget"

    async def prepare_recovery(self, parameters, *, request_id, step, attempt):
        """仅允许预算内且严格缩小的输入恢复；同一模型步骤只允许一次。"""
        if step in self._recovery_steps:
            return "context_window_exceeded"
        self._recovery_steps.add(step)
        measurement = self._meter.measure(parameters)
        before_tokens = measurement["estimated_tokens"]
        measurement = await self._organize(parameters, measurement, step=step, attempt=attempt, recovery=True)
        if measurement["estimated_tokens"] >= before_tokens or not self._within_budget(measurement):
            await self._summarize(parameters, force=True)
        self._measurement = self._measure_view(parameters)
        after_tokens = self._measurement["estimated_tokens"]
        allowed = self._within_budget(self._measurement)
        reduced = after_tokens < before_tokens
        await self._emit("context.recovery_prepared", {"request_id": request_id,
            "before_tokens": before_tokens, "after_tokens": after_tokens,
            "reduced": reduced, "allowed": allowed, "step": step,
            "summary_attempts": self._summary_attempts, "version": self._view.version})
        return None if reduced and allowed else "context_recovery_unavailable"

    async def observe_usage(self, request_id, usage):
        if self._measurement is not None:
            calibration = self._meter.observe_usage(usage, self._measurement)
            await self._emit("context.usage", {"request_id": request_id, **calibration})

    def _fits_tool_results(self, parameters, results):
        """历史回读页面与未完成工具结果一起参与完整请求预算。"""
        if self._policy is None:
            return True
        projected = copy.deepcopy(parameters)
        projected["messages"] = copy.deepcopy(self._view.messages) + [{"role": "user", "content": results}]
        return self._within_budget(self._meter.measure(projected))
