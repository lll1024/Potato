"""仅归一化非行为字段；身份关联与因果顺序先独立校验。"""
from collections import defaultdict
from datetime import datetime
import gzip
import json
import re
from typing import Any

TIME_FIELDS = {"timestamp", "created_at", "updated_at", "started_at", "finished_at", "proposed_at",
               "call_started_at", "call_finished_at", "waiting_at", "retrieved_at", "ended_at"}
CURSOR_FIELDS = {"cursor", "first_event_cursor", "last_event_cursor"}


def validate(data):
    """使用原始身份及序号验证；不允许归一化掩盖悬空引用或逆序。"""
    for name, scenario in data.items():
        if not isinstance(scenario, dict) or "snapshot" not in scenario:
            continue
        saved, payloads = scenario["snapshot"], scenario["payloads"]
        session = saved["session"]["session_id"]
        turns = {turn["turn_id"]: turn for turn in saved["turns"]}
        requests = {request["request_id"]: request for request in saved["requests"]}
        tools = {tool["tool_call_id"]: tool for tool in saved["tool_calls"]}
        events = saved["events"]
        assert len({event["event_id"] for event in events}) == len(events), name
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1)), name
        cursors = [event["cursor"] for event in events]
        assert cursors == sorted(set(cursors)), name
        assert scenario["sse"] == [{"schema_version": 1, **event} for event in events], name
        positions = {}
        for index, event in enumerate(events):
            assert event["session_id"] == session and event["turn_id"] in turns, (name, event)
            request_id, tool_id = event.get("request_id"), event.get("tool_call_id")
            if request_id:
                assert request_id in requests and requests[request_id]["turn_id"] == event["turn_id"], (name, event)
            if tool_id:
                assert tool_id in tools and tools[tool_id]["request_id"] == request_id, (name, event)
            subject = tool_id or request_id or event["turn_id"]
            positions[(subject, event["kind"])] = index
        for request_id, request in requests.items():
            assert request["session_id"] == session and request["turn_id"] in turns, name
            start = positions[(request_id, "request.started")]
            assert positions[(request["turn_id"], "turn.accepted")] < start, name
            for kind in ("request.completed", "request.failed"):
                if (request_id, kind) in positions:
                    assert start < positions[(request_id, kind)], name
                    terminal = positions.get((request["turn_id"], "turn.finished"))
                    if terminal is not None:
                        assert positions[(request_id, kind)] < terminal, name
            previous = [item for item in requests.values() if item["turn_id"] == request["turn_id"]
                        and item["ordinal"] < request["ordinal"]]
            for earlier in previous:
                end = positions.get((earlier["request_id"], "request.completed"),
                                    positions.get((earlier["request_id"], "request.failed")))
                assert end is not None and end < start, name
                for tool_id, tool in tools.items():
                    if tool["request_id"] == earlier["request_id"]:
                        ends = [positions[(tool_id, kind)] for kind in ("tool.completed", "tool.failed", "tool.not_executed")
                                if (tool_id, kind) in positions]
                        assert ends and max(ends) < start, name
        for tool_id, tool in tools.items():
            request_id = tool["request_id"]
            assert request_id in requests and tool["turn_id"] == requests[request_id]["turn_id"], name
            pending = positions[(tool_id, "tool.pending")]
            assert positions[(request_id, "request.completed")] < pending, name
            response = payloads[requests[request_id]["response_payload_id"]]["content"]
            assert any(block.get("id") == tool["tool_use_id"] for block in response["content"]), name
            if (tool_id, "tool.running") in positions:
                assert pending < positions[(tool_id, "tool.running")], name
            for kind in ("tool.completed", "tool.failed", "tool.not_executed"):
                if (tool_id, kind) in positions:
                    assert pending < positions[(tool_id, kind)], name
                    if tool.get("call_started"):
                        assert positions[(tool_id, "tool.running")] < positions[(tool_id, kind)], name
                    terminal = positions.get((tool["turn_id"], "turn.finished"))
                    if terminal is not None:
                        assert positions[(tool_id, kind)] < terminal, name
        for row in [*turns.values(), *requests.values(), *tools.values()]:
            for key, value in row.items():
                if key.endswith("payload_id") and value:
                    assert value in payloads and payloads[value]["payload_id"] == value, (name, key)
            for key, value in row.items():
                if key in TIME_FIELDS and isinstance(value, str):
                    datetime.fromisoformat(value)
        for request in requests.values():
            contents = payloads[request["input_payload_id"]]["content"]["messages"]
            proposed = set()
            for item in contents:
                if isinstance(item["content"], list):
                    for block in item["content"]:
                        if block.get("type") == "tool_use":
                            proposed.add(block["id"])
                        elif block.get("type") == "tool_result":
                            assert block["tool_use_id"] in proposed, (name, block)


def normalize(data):
    validate(data)
    ids, counts = {}, defaultdict(int)

    def bind(value, kind):
        if value and value not in ids:
            counts[kind] += 1
            ids[value] = f"<{kind}:{counts[kind]}>"

    for scenario in data.values():
        if not isinstance(scenario, dict) or "snapshot" not in scenario:
            continue
        saved = scenario["snapshot"]
        bind(saved["session"]["session_id"], "session")
        bind(saved["stream_id"], "stream")
        for kind, rows in (("turn", saved["turns"]), ("request", saved["requests"]), ("tool_call", saved["tool_calls"])):
            for row in rows:
                bind(row[kind + "_id"], kind)
                bind(row.get("retry_id"), "retry")
                for key in sorted(row):
                    if key.endswith("payload_id"):
                        bind(row[key], "payload")
        # 事件按主体、事件种类与主体内序号命名，允许独立查询真实完成次序变化。
        subjects = defaultdict(int)
        for event in saved["events"]:
            subject = event.get("tool_call_id") or event.get("request_id") or event["turn_id"]
            subjects[subject] += 1
            ids.setdefault(event["event_id"], f"<event:{ids[subject]}:{subjects[subject]}>")
    for preference in data.get("preferences", {}).get("preferences", []):
        bind(preference["id"], "preference")
    pattern = re.compile("|".join(re.escape(key) for key in ids)) if ids else None
    times = set()

    def find_source_times(value):
        if isinstance(value, dict):
            if value.get("provider") == "exa" and isinstance(value.get("retrieved_at"), str):
                times.add(value["retrieved_at"])
            for child in value.values():
                find_source_times(child)
        elif isinstance(value, list):
            for child in value:
                find_source_times(child)
        elif isinstance(value, str) and value.startswith(("{", "[")):
            try:
                find_source_times(json.loads(value))
            except ValueError:
                pass
    for scenario in data.values():
        if isinstance(scenario, dict) and "snapshot" in scenario:
            saved = scenario["snapshot"]
            records = [saved["session"], *saved["turns"], *saved["requests"], *saved["tool_calls"],
                       *saved["events"], *(event["data"] for event in saved["events"])]
            for record in records:
                times.update(value for key, value in record.items() if key in TIME_FIELDS and isinstance(value, str))
    times.update(item["updated_at"] for item in data.get("preferences", {}).get("preferences", []))
    find_source_times(data)

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            return {ids.get(name, name): visit(child) for name, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [visit(child) for child in value]
        if isinstance(value, str):
            if value.startswith(("{", "[")):
                try:
                    return json.dumps(visit(json.loads(value)), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                except ValueError:
                    pass
            value = pattern.sub(lambda match: ids[match[0]], value) if pattern else value
            for timestamp in times:
                value = value.replace(timestamp, "<timestamp>")
            return value
        return value

    def metadata(record: dict, *, event=False) -> dict:
        result = visit(record)
        for key, value in record.items():
            if value is not None:
                if key in TIME_FIELDS:
                    result[key] = "<timestamp>"
                elif key.endswith("duration_ms"):
                    result[key] = "<duration>"
                elif key in CURSOR_FIELDS:
                    result[key] = "<cursor>"
        if event:
            result.pop("sequence")
            result["data"] = metadata(record["data"])
        return result

    result = visit(data)
    for name, scenario in data.items():
        if not isinstance(scenario, dict) or "snapshot" not in scenario:
            continue
        saved = scenario["snapshot"]
        normalized = metadata(saved)
        normalized["session"] = metadata(saved["session"])
        for key in ("turns", "requests", "tool_calls"):
            normalized[key] = [metadata(record) for record in saved[key]]
        # 仅真实轨迹元数据归一化；模型输入和工具参数中的同名业务字段原样比较。
        for key, events in (("events", saved["events"]), ("sse", scenario["sse"])):
            buckets = defaultdict(list)
            for event in events:
                subject = event.get("tool_call_id") or event.get("request_id") or event["turn_id"]
                buckets[ids[subject]].append(metadata(event, event=True))
            if key == "events":
                normalized[key] = dict(buckets)
            else:
                result[name][key] = dict(buckets)
        result[name]["snapshot"] = normalized
    if "preferences" in result:
        result["preferences"]["preferences"] = [metadata(item) for item in data["preferences"]["preferences"]]
    return result


def read_capture(path):
    return json.loads(gzip.decompress(path.read_bytes()))
