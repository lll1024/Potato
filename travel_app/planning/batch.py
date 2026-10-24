"""混合工具批次的唯一协调点：外部段、历史屏障、预算及结果原序。"""
import asyncio
import copy
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any
from anthropic.types import ToolResultBlockParam
from travel_app.context import HISTORY_TOOLS
from travel_app.retrieval import Query, ToolResult

BUDGET_MESSAGE = "已达到本轮查询上限，查询尚未全部完成，请缩小范围后继续。"


async def execute_batch(tool_calls, *, request_id, parameters, context, tools, budget,
                        observer, stop_requested, requested_reason):
    results: list[ToolResultBlockParam] = []
    outcomes: list[ToolResult] = []
    tool_errors = 0
    tool_ids = [uuid4().hex for _ in tool_calls]
    for tool_ordinal, (block, tool_id) in enumerate(zip(tool_calls,tool_ids),1):
        if observer:
            await observer("tool.pending",{"tool_call_id":tool_id,"request_id":request_id,
                "ordinal":tool_ordinal,"tool_use_id":block.id,"name":block.name,
                "arguments":copy.deepcopy(block.input),"status":"pending",
                "proposed_at":datetime.now(timezone.utc).isoformat(),"call_started":False,
                "call_duration_ms":None,"wait_duration_ms":None,"total_duration_ms":None})
    # 各任务固定身份；额度在创建并发任务之前按提出顺序预留。
    batch_stop = stop_requested if stop_requested is not None else asyncio.Event()

    def observe_tool(index):
        block, tool_id = tool_calls[index], tool_ids[index]
        async def tool_observer(kind: str, data: dict[str, Any]) -> None:
            if kind == "tool.not_executed" and batch_stop.is_set():
                data["reason"] = requested_reason()
            if "result" in data:
                output_result = data["result"]
                data["result"] = {"type": "tool_result", "tool_use_id": block.id,
                    "content": output_result["content"], "is_error": output_result["is_error"]}
            if observer:
                try:
                    await observer(kind, {**data, "tool_call_id": tool_id, "request_id": request_id})
                except Exception:
                    batch_stop.set()
                    raise
        return tool_observer

    async def execute(index, allowed):
        block = tool_calls[index]
        tool_observer = observe_tool(index)
        if batch_stop.is_set() or not allowed:
            output: ToolResult = {"content": "未执行：已停止本轮查询。" if batch_stop.is_set() else BUDGET_MESSAGE,
                                  "is_error": True, "not_executed": True}
            await tool_observer("tool.not_executed", {"status": "not_executed",
                "reason": requested_reason() if batch_stop.is_set() else "budget",
                "result": output, "finished_at": datetime.now(timezone.utc).isoformat()})
            return output
        return await context.read_history(block.name, block.input, parameters=parameters,
                completed=results, pending=[pending.id for pending in tool_calls[index + 1:]],
            tool_use_id=block.id, observer=tool_observer if observer else None)


    def append_result(index, output):
        nonlocal tool_errors
        if output["is_error"] and not output.get("not_executed"):
            tool_errors += 1
        outcomes.append(output)
        results.append({"type": "tool_result", "tool_use_id": tool_calls[index].id,
                        "content": output["content"], "is_error": output["is_error"]})

    index = 0
    while index < len(tool_calls):
        if tool_calls[index].name in HISTORY_TOOLS:
            allowed = budget.reserve_tool()
            output = await execute(index, allowed)
            if allowed and output.get("not_executed"):
                budget.release_tool()
            append_result(index, output)
            index += 1
            continue
        start = index
        pending = []
        while index < len(tool_calls) and tool_calls[index].name not in HISTORY_TOOLS:
            allowed = budget.reserve_tool()
            pending.append((index, allowed))
            index += 1
        queries = [Query(tool_calls[position].id, tool_calls[position].name, tool_calls[position].input)
                   for position, allowed in pending if allowed]
        async def observe_external(kind, data):
            identity = data.pop("query_id")
            position = next(position for position in range(start, index) if tool_calls[position].id == identity)
            await observe_tool(position)(kind, data)
        async def skipped():
            return [(position, await execute(position, False)) for position, allowed in pending if not allowed]
        # 拒绝项也有事实保存边界；任一保存故障先阻止新启，仍等待在途返回与保存。
        completed = await asyncio.gather(
            tools.execute(queries, observer=observe_external, stop_requested=batch_stop), skipped(),
            return_exceptions=True)
        for output in completed:
            if isinstance(output, BaseException):
                raise output
        external, rejected = completed
        by_id = {item.query_id: item for item in external}
        rejected = dict(rejected)
        for position, allowed in pending:
            if allowed:
                item = by_id[tool_calls[position].id]
                output = {"content": item.content, "is_error": item.is_error, "not_executed": item.not_executed}
            else:
                output = rejected[position]
            append_result(position, output)
    return results, outcomes, tool_errors
