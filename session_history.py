"""仅公开当前助手会话的唯一原文记录；请求快照和系统输入不属于历史。"""
import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Callable

HISTORY_TOOLS = {"session_history_search", "session_history_read"}
HISTORY_DECLARATIONS = [
    {"name": "session_history_search", "description": "按关键词搜索当前助手会话历史；最多 5 条、每条 200 字符。旧资料须重新核对适用日期、来源及核实状态。", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}},
    {"name": "session_history_read", "description": "按搜索或摘要中的 record_id/protocol_reference 分页读取当前助手会话原文；每页最多 4000 字符，next_offset 可继续读取。历史资料不等于当前已核实事实。", "input_schema": {"type": "object", "properties": {"record_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 4000}}, "required": ["record_id"], "additionalProperties": False}},
]


def source_metadata(text):
    """把原文已有的来源/日期/核实边界随页面携带，不生成新的核实结论。"""
    try:
        content = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(content, dict):
        return {}
    metadata = {key: content[key] for key in ("retrieved_at", "document_status", "content_boundary", "max_characters", "travel_dates", "applicable_dates", "verification_status") if key in content}
    if isinstance(content.get("sources"), list):
        metadata["sources"] = [{key: source[key] for key in ("url", "title", "published_date", "content_type", "retrieved_at", "applicable_dates", "verification_status") if key in source}
                               for source in content["sources"] if isinstance(source, dict)]
    return {"source_metadata": metadata} if metadata else {}


def protocol_records(messages):
    """CLI 索引仅从当前进程的原始协议读取；标识永远不引用请求快照。"""
    records = []
    turn = 0
    names = {}
    for index, message in enumerate(messages, 1):
        content = message.get("content")
        if message["role"] == "user" and isinstance(content, str):
            turn += 1
            records.append({"record_id": f"message:{index}", "protocol_reference": f"message:{index}", "kind": "turn_input", "text": content, "turn_ordinal": turn, "source": "user", "history_status": "recorded"})
        elif message["role"] == "assistant" and isinstance(content, str):
            records.append({"record_id": f"message:{index}", "protocol_reference": f"message:{index}", "kind": "assistant_text", "text": content, "turn_ordinal": turn, "source": "assistant", "history_status": "recorded"})
        elif isinstance(content, list):
            for block_index, block in enumerate(content):
                if block.get("type") == "tool_use":
                    names[block["id"]] = block["name"]
                elif block.get("type") == "text" and message["role"] == "assistant":
                    records.append({"record_id": f"message:{index}:block:{block_index}", "protocol_reference": f"message:{index}:block:{block_index}", "kind": "assistant_text", "text": block["text"], "turn_ordinal": turn, "source": "assistant", "history_status": "recorded"})
                elif block.get("type") == "tool_result" and names.get(block["tool_use_id"]) not in HISTORY_TOOLS:
                    text = block.get("content", "")
                    records.append({"record_id": f"message:{index}:block:{block_index}", "protocol_reference": f"message:{index}:block:{block_index}", "kind": "tool_result", "text": text if isinstance(text, str) else json.dumps(text, ensure_ascii=False), "turn_ordinal": turn, "source": names.get(block["tool_use_id"], "tool"), "history_status": "failed" if block.get("is_error") else "completed"})
    return records


def bind_protocol_references(records, messages):
    """将白名单 payload 身份关联完整协议位置；压缩视图位置不可用于此映射。"""
    available = protocol_records(messages)
    for record in records:
        for position, candidate in enumerate(available):
            if candidate["kind"] == record["kind"] and candidate["text"] == record["text"]:
                record["protocol_reference"] = candidate["protocol_reference"]
                available.pop(position)
                break
    return records


class SessionHistory:
    def __init__(self, records: Callable[[], list[dict[str, Any]]]):
        self.records = records
        self.calls = 0

    async def call(self, name, arguments, *, observer=None, fits=lambda content: True):
        started = asyncio.get_running_loop().time()
        timestamp = datetime.now(timezone.utc).isoformat()
        if self.calls >= 8:
            output = {"content": "未执行：本轮历史搜索与读取已达 8 次上限。", "is_error": True, "not_executed": True}
            if observer:
                await observer("tool.not_executed", {"status": "not_executed", "reason": "history_budget", "result": output, "finished_at": timestamp, "call_started": False})
            return output
        self.calls += 1
        if observer:
            await observer("tool.running", {"status": "running", "started_at": timestamp, "call_started_at": timestamp, "call_started": True, "wait_duration_ms": 0.0})
        try:
            records = self.records()
            if not isinstance(arguments, dict):
                raise ValueError("历史工具参数必须是对象。")
            if name == "session_history_search":
                query = arguments.get("query")
                if not isinstance(query, str) or not query.strip() or set(arguments) - {"query"}:
                    raise ValueError("请提供非空 query 关键词。")
                matches = []
                for record in records:
                    offset = record["text"].find(query)
                    if offset >= 0:
                        start = max(0, offset - 40)
                        matches.append({**self.metadata(record), "excerpt": record["text"][start:start + 200], "offset": start, "end_offset": min(len(record["text"]), start + 200)})
                        if len(matches) == 5:
                            break
                result = {"query": query, "matches": matches, "verification_status": "historical_requires_recheck"}
                while matches and not fits(json.dumps(result, ensure_ascii=False)):
                    matches.pop()
            else:
                identifier = arguments.get("record_id")
                offset, limit = arguments.get("offset", 0), arguments.get("limit", 4000)
                if not isinstance(identifier, str) or any(not isinstance(value, int) or isinstance(value, bool) for value in (offset, limit)) or offset < 0 or limit < 1 or set(arguments) - {"record_id", "offset", "limit"}:
                    raise ValueError("请提供 record_id 与非负 offset、正整数 limit。")
                record = next((item for item in records if identifier in (item["record_id"], item.get("protocol_reference"))), None)
                if record is None and identifier.startswith("message:") and identifier.count(":") == 1:
                    blocks = [item for item in records if item.get("protocol_reference", "").startswith(identifier + ":block:")]
                    if len(blocks) == 1:
                        record = blocks[0]
                    elif blocks:
                        # 一个原始协议消息可能包含多个工具结果；消息引用回读全部允许的块。
                        record = {"record_id": identifier, "protocol_reference": identifier,
                            "kind": "protocol_message", "turn_ordinal": blocks[0]["turn_ordinal"],
                            "source": "history", "history_status": "recorded",
                            "records": [self.metadata(item) for item in blocks],
                            "text": "\n\n".join(item["text"] for item in blocks)}
                if record is None:
                    raise ValueError("记录不存在、类型不可读取或不属于当前助手会话。")
                if offset > len(record["text"]):
                    raise ValueError("offset 超出原文范围。")
                end = min(len(record["text"]), offset + min(limit, 4000))
                def page(end):
                    return {**self.metadata(record), "text": record["text"][offset:end], "offset": offset, "end_offset": end, "total": len(record["text"]), "next_offset": end if end < len(record["text"]) else None, "encoding": "unicode_code_points"}
                result = page(end)
                if not fits(json.dumps(result, ensure_ascii=False)):
                    low, high = offset, end
                    while low < high:
                        middle = (low + high + 1) // 2
                        if fits(json.dumps(page(middle), ensure_ascii=False)):
                            low = middle
                        else:
                            high = middle - 1
                    result = page(low)
                    if low == offset and offset < len(record["text"]):
                        raise ValueError("当前完整模型上下文余量不足以读取原文，请先压缩历史。")
            if not fits(json.dumps(result, ensure_ascii=False)):
                raise ValueError("当前完整模型上下文余量不足以容纳历史结果。")
            output = {"content": json.dumps(result, ensure_ascii=False), "is_error": False}
        except ValueError as error:
            output = {"content": str(error), "is_error": True}
        if observer:
            duration = max(0, (asyncio.get_running_loop().time() - started) * 1000)
            await observer("tool.failed" if output["is_error"] else "tool.completed", {"status": "failed" if output["is_error"] else "completed", "result": output, "finished_at": datetime.now(timezone.utc).isoformat(), "call_finished_at": datetime.now(timezone.utc).isoformat(), "call_duration_ms": duration, "total_duration_ms": duration, "wait_duration_ms": 0.0, "call_started": True})
        return output

    @staticmethod
    def metadata(record):
        return {**{key: value for key, value in record.items() if key != "text"},
                **source_metadata(record["text"]), "verification_status": "historical_requires_recheck"}
