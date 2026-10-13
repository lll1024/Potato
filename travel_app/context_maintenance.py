"""集中准备模型上下文；压力与超限恢复共享本轮视图、计量及摘要额度。"""
import copy
import os
from typing import Any

from travel_app.context_summary import earlier_history_cutoff, summarize_history
from travel_app.model_context import TokenMeter


class ContextMaintenance:
    def __init__(self, *, view, policy, model, client, tools, date_context, turn_start,
                 next_request_ordinal, observer, stop_requested):
        self.view = view
        self.policy = policy
        self.meter = TokenMeter(model, usage_semantics="anthropic" if not os.getenv("ANTHROPIC_BASE_URL") else "compatible_unknown")
        self.client, self.tools = client, tools
        self.date_context, self.turn_start = date_context, turn_start
        self.next_request_ordinal = next_request_ordinal
        self.observer, self.stop_requested = observer, stop_requested
        self.summary_attempts = 0
        self.summary_blocked = False
        self.measurement: dict[str, Any] | None = None

    async def _emit(self, kind, data):
        if self.observer:
            await self.observer(kind, data)

    def _measure_view(self, parameters):
        parameters["messages"] = copy.deepcopy(self.view.messages)
        return self.meter.measure(parameters)

    def _within_budget(self, measurement):
        return self.policy is not None and measurement["estimated_tokens"] <= self.policy.input_budget

    async def _organize(self, parameters, measurement, *, step, attempt, recovery=False):
        before_tokens = measurement["estimated_tokens"]
        changed = self.view.organize_tool_results()
        measurement = self._measure_view(parameters)
        data = {"before_tokens": before_tokens, "after_tokens": measurement["estimated_tokens"],
                "changed_result_count": changed,
                "pressure_remaining": self.policy is None or measurement["estimated_tokens"] >= self.policy.pressure_threshold,
                "original_watermark": self.view.original_watermark, "version": self.view.version,
                "record_ids": self.view.record_ids.copy(), "step": step, "attempt": attempt}
        if not recovery:
            assert self.policy is not None
            data["pressure_threshold"] = self.policy.pressure_threshold
        await self._emit("context.organized", data)
        return measurement

    async def _summarize(self, parameters, *, force=False):
        if self.policy is None or self.summary_attempts >= 4 or self.summary_blocked:
            return
        result = await summarize_history(
            view=self.view, policy=self.policy, meter=self.meter, parameters=parameters,
            client=self.client, tools=self.tools, date_context=self.date_context,
            cutoff=earlier_history_cutoff(self.view, self.turn_start, self.policy, self.meter, force=force),
            remaining_attempts=4 - self.summary_attempts, next_request_ordinal=self.next_request_ordinal,
            observer=self.observer, stop_requested=self.stop_requested)
        self.summary_attempts += result.attempts
        self.summary_blocked = self.summary_blocked or result.terminal_failure

    async def prepare_request(self, parameters, *, step, attempt, recovering=False):
        """更新可发送的输入；返回预算阻止原因，关闭压缩时沿用原输入。"""
        measurement = self.meter.measure(parameters) if self.policy else None
        self.measurement = measurement
        if self.policy is None:
            return None
        assert measurement is not None
        if not recovering and measurement["estimated_tokens"] >= self.policy.pressure_threshold:
            measurement = await self._organize(parameters, measurement, step=step, attempt=attempt)
            if (measurement["estimated_tokens"] >= self.policy.pressure_threshold
                    and self.summary_attempts < 4 and not self.summary_blocked):
                await self._summarize(parameters)
                measurement = self._measure_view(parameters)
        self.measurement = measurement
        allowed = self._within_budget(measurement)
        await self._emit("context.checked", {**measurement, "window": self.policy.window,
            "input_budget": self.policy.input_budget, "allowed": allowed,
            "original_watermark": self.view.original_watermark, "version": self.view.version,
            "record_ids": self.view.record_ids.copy(), "step": step, "attempt": attempt})
        return None if allowed else "context_budget"

    async def prepare_recovery(self, parameters, *, request_id, step, attempt):
        """仅允许预算内且严格缩小的输入恢复；调用方负责原步骤的一次恢复。"""
        measurement = self.meter.measure(parameters)
        before_tokens = measurement["estimated_tokens"]
        measurement = await self._organize(parameters, measurement, step=step, attempt=attempt, recovery=True)
        if measurement["estimated_tokens"] >= before_tokens or not self._within_budget(measurement):
            await self._summarize(parameters, force=True)
        self.measurement = self._measure_view(parameters)
        after_tokens = self.measurement["estimated_tokens"]
        allowed = self._within_budget(self.measurement)
        reduced = after_tokens < before_tokens
        await self._emit("context.recovery_prepared", {"request_id": request_id,
            "before_tokens": before_tokens, "after_tokens": after_tokens,
            "reduced": reduced, "allowed": allowed, "step": step,
            "summary_attempts": self.summary_attempts, "version": self.view.version})
        return None if reduced and allowed else "context_recovery_unavailable"

    async def observe_usage(self, request_id, usage):
        if self.measurement is not None:
            calibration = self.meter.observe_usage(usage, self.measurement)
            await self._emit("context.usage", {"request_id": request_id, **calibration})

    def fits_tool_results(self, parameters, results):
        """历史回读页面与未完成工具结果一起参与完整请求预算。"""
        if self.policy is None:
            return True
        projected = copy.deepcopy(parameters)
        projected["messages"] = copy.deepcopy(self.view.messages) + [{"role": "user", "content": results}]
        return self._within_budget(self.meter.measure(projected))
