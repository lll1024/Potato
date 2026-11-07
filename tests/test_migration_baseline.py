"""固定迁移前行为与合成旧数据；测试只经公开 HTTP/SSE 和真实进程。"""
import gzip
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from compatibility.capture import FIXTURES, ROOT, ScenarioServices, capture, service, snapshot, submit
from compatibility.compare import normalize, read_capture, validate


QUERY_PHASE_RULE = "\n资料获取阶段：仅按需查询；仅在准备调用查询工具时，以“进展：”开头，用不超过120字的一句简短中文向旅行者说明准备做的行动；不展示内部推理、参数或原始载荷，不声称尚未完成的查询成功。不需查询或资料齐全时只返回“无需查询”，不写旅行行程或最终答案。"


def streaming_projection(actual):
    """只剥离 #98／#101／#111／#112 明确新增的流式和进展事实；固定数据与通用 normalize 不变。"""
    validate(actual)
    data = copy.deepcopy(actual)
    expected_streams = {}
    expected_queries = {}
    phase_rule = QUERY_PHASE_RULE
    for scenario in data.values():
        if not isinstance(scenario, dict) or "snapshot" not in scenario:
            continue
        saved, payloads = scenario["snapshot"], scenario["payloads"]
        # 固定场景没有显式模型行动说明，只允许这两项真实工具的可靠进展。
        actions = {"maps_text_search": "查找地点资料", "web_search_exa": "查找网页资料"}
        states = {"tool.pending": "pending", "tool.waiting": "pending", "tool.running": "running",
                  "tool.completed": "completed", "tool.failed": "failed", "tool.not_executed": "not_executed"}
        tools = {tool["tool_call_id"]: tool for tool in saved["tool_calls"]}
        progress = {turn["turn_id"]: {} for turn in saved["turns"]}
        for event in saved["events"]:
            if event["kind"] in states:
                tool = tools[event["tool_call_id"]]
                entry = {"id": tool["tool_call_id"], "attempt_id": tool["request_id"], "kind": "tool",
                         "text": actions[tool["name"]], "status": states[event["kind"]]}
                assert event["data"].pop("progress") == entry
                progress[event["turn_id"]][entry["id"]] = entry
            else:
                assert "progress" not in event["data"]
        for tool in tools.values():
            assert tool.pop("progress") == progress[tool["turn_id"]][tool["tool_call_id"]]
        for turn in saved["turns"]:
            entries = list(progress[turn["turn_id"]].values())
            if turn["reason"] == "service_interrupted":
                entries = [{**entry, "status": "interrupted"} if entry["status"] in {"pending", "running"}
                           else entry for entry in entries]
            assert turn.pop("query_progress") == entries
        answers = [r for r in saved["requests"] if r["purpose"] == "travel_answer"]
        for turn in saved["turns"]:
            delivery = turn.pop("answer_delivery", None)
            assert delivery == ("stream" if any(r["turn_id"] == turn["turn_id"] for r in answers) else None)
        identifiers = {r["request_id"] for r in answers}
        steps = {(r["turn_id"], r["step"]) for r in answers}
        assert all(count == 1 for count in Counter(r["turn_id"] for r in answers).values())
        for request in answers:
            parameters = payloads[request["input_payload_id"]]["content"]
            assert request["request_id"] not in expected_streams or expected_streams[request["request_id"]] == parameters
            expected_streams[request["request_id"]] = copy.deepcopy(parameters)
            assert "tools" not in parameters
            assert request["status"] == "completed" and request["ordinal"] <= 63
            turn = next(t for t in saved["turns"] if t["turn_id"] == request["turn_id"])
            reply = payloads[request["response_payload_id"]]["content"]
            assert turn["answer"] == "\n".join(b["text"] for b in reply["content"])
            for key, value in request.items():
                if key.endswith("payload_id") and value:
                    del payloads[value]
        saved["requests"] = [r for r in saved["requests"] if r["request_id"] not in identifiers]
        for owner, removed in [(saved, answers), *[(t, [r for r in answers if r["turn_id"] == t["turn_id"]]) for t in saved["turns"]]]:
            for metric, summary in owner["usage_summary"].items():
                summary["request_count"] -= len(removed)
                for request in removed:
                    value = (request.get("usage") or {}).get(metric)
                    if isinstance(value, (int, float)):
                        summary["known_count"] -= 1
                        summary["value"] -= value
        checked_steps = Counter()
        query_steps = {(r["turn_id"], r["step"]) for r in saved["requests"] if r.get("answer_phase") == "query"}
        query_checks = Counter()
        kept_events = []
        for event in saved["events"]:
            if event.get("request_id") in identifiers:
                assert event["kind"] in ("request.started", "request.completed", "context.usage")
                continue
            if event["kind"] == "context.checked" and (event["turn_id"], event["data"].get("step")) in steps:
                checked_steps[(event["turn_id"], event["data"]["step"])] += 1
                assert event["data"]["allowed"] is True
                assert event["data"]["estimated_tokens"] <= event["data"]["input_budget"]
                continue
            if event["kind"] == "context.checked" and (event["turn_id"], event["data"].get("step")) in query_steps:
                query_checks[(event["turn_id"], event["data"]["step"])] += 1
                assert event["data"]["allowed"] is True
                assert event["data"]["estimated_tokens"] <= event["data"]["input_budget"]
            if event["kind"] == "request.started":
                assert event["data"].pop("answer_phase", "query") == "query"
            kept_events.append(event)
        assert checked_steps == Counter({step: 1 for step in steps})
        assert query_checks == Counter({step: 1 for step in query_steps})
        for request in saved["requests"]:
            phase = request.pop("answer_phase", None)
            parameters = payloads[request["input_payload_id"]]["content"]
            if phase == "query":
                assert request["purpose"] == "travel" and request["ordinal"] <= 63
                assert request["request_id"] not in expected_queries or expected_queries[request["request_id"]] == parameters
                expected_queries[request["request_id"]] = copy.deepcopy(parameters)
                assert parameters["system"].count(phase_rule) == 1
                parameters["system"] = parameters["system"].replace(phase_rule, "", 1)
                if request["response_payload_id"]:
                    reply = payloads[request["response_payload_id"]]["content"]
                    # SDK 流式聚合显式补齐这两个空停止字段，非空变化不能被投影。
                    assert reply.pop("stop_sequence") is None
                    assert reply.pop("stop_details") is None
            else:
                assert phase_rule not in parameters["system"]
        saved["events"] = kept_events
        for index, event in enumerate(saved["events"], 1):
            event["sequence"] = index
            if event["kind"] in ("context.checked", "context.organized", "context.adopted"):
                # 公开 HTTP 采集与固定基线独立对照：本次查询规则增加 258 tokens。
                assert event["data"].get("method", "conservative_unicode") == "conservative_unicode"
                for field in ("estimated_tokens", "raw_estimated_tokens", "before_tokens", "after_tokens"):
                    if field in event["data"]:
                        event["data"][field] -= 258
        scenario["sse"] = [{"schema_version": 1, **e} for e in saved["events"]]
    def serialized(parameters):
        return json.dumps(parameters, sort_keys=True, ensure_ascii=False)
    expected = {**expected_streams, **expected_queries}
    external_streams = [{k: v for k, v in r.items() if k != "stream"} for r in data["model_requests"] if r.get("stream")]
    assert Counter(map(serialized, external_streams)) == Counter(map(serialized, expected.values()))
    data["stopping"].pop("instance_id", None)

    queries = Counter(map(serialized, expected_queries.values()))
    answers = Counter(map(serialized, expected_streams.values()))
    retained = []
    for request in data["model_requests"]:
        if request.get("stream"):
            parameters = {key: value for key, value in request.items() if key != "stream"}
            key = serialized(parameters)
            if queries[key]:
                queries[key] -= 1
                parameters["system"] = parameters["system"].replace(phase_rule, "", 1)
                retained.append(parameters)
            else:
                assert answers[key] > 0
                answers[key] -= 1
        else:
            assert phase_rule not in request["system"]
            retained.append(request)
    assert not any(queries.values()) and not any(answers.values())
    data["model_requests"] = retained
    return data


class MigrationBaselineTests(unittest.IsolatedAsyncioTestCase):
    maxDiff = 3000

    async def test_public_web_sse_cli_and_process_behaviors_match_fixed_baseline(self):
        baseline = read_capture(FIXTURES / "behavior.json.gz")
        with tempfile.TemporaryDirectory() as directory:
            actual = await capture(ROOT, Path(directory))
        # 新阶段投影必须拒绝未记账的真实流调用及超硬容量的新增作答。
        unaccounted = copy.deepcopy(actual)
        unaccounted["model_requests"].append(next(r for r in actual["model_requests"] if r.get("stream")))
        with self.assertRaises(AssertionError):
            streaming_projection(unaccounted)
        wrong_delivery = copy.deepcopy(actual)
        wrong_delivery["normal"]["snapshot"]["turns"][0]["answer_delivery"] = "complete"
        with self.assertRaises(AssertionError):
            streaming_projection(wrong_delivery)
        for purpose in ("travel", "travel_answer"):
            with self.subTest(rejected_capacity=purpose):
                unsafe = copy.deepcopy(actual)
                scenario = unsafe["normal"]
                step = next(r["step"] for r in scenario["snapshot"]["requests"] if r["purpose"] == purpose)
                for event in scenario["snapshot"]["events"]:
                    if event["kind"] == "context.checked" and event["data"]["step"] == step:
                        event["data"]["allowed"] = False
                        event["data"]["estimated_tokens"] = event["data"]["input_budget"] + 1
                scenario["sse"] = [{"schema_version": 1, **e} for e in scenario["snapshot"]["events"]]
                with self.assertRaises(AssertionError):
                    streaming_projection(unsafe)
            with self.subTest(missing_capacity_check=purpose):
                missing = copy.deepcopy(actual)
                scenario = missing["normal"]
                scenario["snapshot"]["events"] = [e for e in scenario["snapshot"]["events"]
                    if not (e["kind"] == "context.checked" and e["data"]["step"] == step)]
                for index, event in enumerate(scenario["snapshot"]["events"], 1):
                    event["sequence"] = index
                scenario["sse"] = [{"schema_version": 1, **e} for e in scenario["snapshot"]["events"]]
                with self.assertRaises(AssertionError):
                    streaming_projection(missing)
        unknown_rule = copy.deepcopy(actual)
        scenario = unknown_rule["normal"]
        query = next(r for r in scenario["snapshot"]["requests"] if r.get("answer_phase") == "query")
        parameters = scenario["payloads"][query["input_payload_id"]]["content"]
        parameters["system"] = parameters["system"].replace("不超过120字", "任意长度")
        with self.assertRaises(AssertionError):
            streaming_projection(unknown_rule)
        changed_stop = copy.deepcopy(actual)
        scenario = changed_stop["normal"]
        query = next(r for r in scenario["snapshot"]["requests"] if r.get("answer_phase") == "query")
        scenario["payloads"][query["response_payload_id"]]["content"]["stop_details"] = {"reason": "unknown"}
        with self.assertRaises(AssertionError):
            streaming_projection(changed_stop)
        changed_delivery = copy.deepcopy(actual)
        query_call = next(r for r in changed_delivery["model_requests"] if r.get("stream") and "tools" in r)
        query_call["stream"] = False
        with self.assertRaises(AssertionError):
            streaming_projection(changed_delivery)
        polluted = copy.deepcopy(actual)
        summary = next(r for r in polluted["model_requests"] if r["system"].startswith("整理旅行状态"))
        summary["system"] += QUERY_PHASE_RULE
        with self.assertRaises(AssertionError):
            streaming_projection(polluted)
        wrong_history = copy.deepcopy(actual)
        wrong_history["normal"]["snapshot"]["turns"][0]["query_progress"][0]["status"] = "failed"
        with self.assertRaises(AssertionError):
            streaming_projection(wrong_history)
        wrong_action = copy.deepcopy(actual)
        wrong_action["normal"]["snapshot"]["tool_calls"][0]["progress"]["text"] = "已完成信息核实"
        with self.assertRaises(AssertionError):
            streaming_projection(wrong_action)
        wrong_identity = copy.deepcopy(actual)
        scenario = wrong_identity["normal"]
        event = next(e for e in scenario["snapshot"]["events"] if e["kind"] == "tool.running")
        event["data"]["progress"]["attempt_id"] = "unrelated-request"
        scenario["sse"] = [{"schema_version": 1, **e} for e in scenario["snapshot"]["events"]]
        with self.assertRaises(AssertionError):
            streaming_projection(wrong_identity)
        fake_model_progress = copy.deepcopy(actual)
        scenario = fake_model_progress["normal"]
        event = next(e for e in scenario["snapshot"]["events"] if e["kind"] == "request.completed")
        event["data"]["progress"] = {"kind": "model", "text": "并不存在的进展"}
        scenario["sse"] = [{"schema_version": 1, **e} for e in scenario["snapshot"]["events"]]
        with self.assertRaises(AssertionError):
            streaming_projection(fake_model_progress)
        for name, scenario in actual.items():
            if isinstance(scenario, dict) and "snapshot" in scenario:
                observed_check = next(e["data"] for e in scenario["snapshot"]["events"] if e["kind"] == "context.checked")
                original_check = next(e["data"] for e in baseline[name]["snapshot"]["events"] if e["kind"] == "context.checked")
                self.assertEqual(observed_check["method"], "conservative_unicode")
                for field in ("estimated_tokens", "raw_estimated_tokens"):
                    self.assertEqual(observed_check[field] - original_check[field], 258)
        expected, observed = normalize(baseline), normalize(streaming_projection(actual))
        # 按场景分别报告，避免巨型快照 diff 掩盖实际变化。
        self.assertEqual(expected.keys(), observed.keys())
        for scenario in expected:
            with self.subTest(scenario=scenario):
                self.assertEqual(observed[scenario], expected[scenario])

    async def test_baseline_database_reads_traces_preferences_and_continues_saved_context(self):
        manifest = json.loads((FIXTURES / "manifest.json").read_text())
        self.assertEqual(manifest["source_commit"], "4e01678fdb036f034d72a858fadb226ea239a0c1")
        raw = (FIXTURES / "legacy.sql.gz").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["fixture_sha256"])
        external = ScenarioServices()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with sqlite3.connect(folder / "travel.sqlite3") as database:
                database.executescript(gzip.decompress(raw).decode())
            clock = folder / "clock.txt"
            clock.write_text("13601")
            async with external.serving() as url:
                async with service(ROOT, folder, url, clock) as (_, client):
                    session = manifest["session_id"]
                    before = await snapshot(client, session)
                    self.assertTrue(before["snapshot"]["requests"])
                    self.assertTrue(any(event["kind"] == "context.saved" for event in before["snapshot"]["events"]))
                    self.assertEqual((await client.get("/api/preferences")).json()["preferences"][0]["content"], "不吃辣")
                    interrupted = (await client.get("/api/sessions/" + manifest["interrupted_session_id"])).json()
                    self.assertEqual(interrupted["turns"][0]["reason"], "service_interrupted")
                    self.assertEqual(len(external.model_calls), 0)
                    await submit(client, "继续旧助手会话", session)
                    after = await snapshot(client, session)
                    self.assertEqual(after["snapshot"]["turns"][-1]["status"], "completed")
                    request = external.model_calls[-1]
                    self.assertIn("travel_state_summary", str(request["messages"]))
                    self.assertIn("西湖酒店", str(request["messages"]))
                    self.assertIn("不吃辣", request["system"])
                    self.assertIn("2026-10-24", request["system"])
                    self.assertEqual(after["snapshot"]["turns"][:-1], before["snapshot"]["turns"])


class BaselineIntegrityTests(unittest.TestCase):
    def test_model_visible_fields_are_never_removed_as_event_metadata(self):
        data = read_capture(FIXTURES / "behavior.json.gz")
        for key, value in (("sequence", 999), ("duration_ms", 42), ("cursor", 1), ("timestamp", "用户时间")):
            with self.subTest(field=key):
                changed = copy.deepcopy(data)
                changed["model_requests"][0]["messages"][0][key] = value
                self.assertNotEqual(normalize(data), normalize(changed))

    def reorder(self, data, events):
        for index, event in enumerate(events, 1):
            event["sequence"] = index
            event["cursor"] = index
        data["normal"]["snapshot"]["events"] = events
        data["normal"]["sse"] = [{"schema_version": 1, **event} for event in events]

    def test_next_model_step_waits_for_all_previous_tool_results(self):
        data = read_capture(FIXTURES / "behavior.json.gz")
        events = data["normal"]["snapshot"]["events"]
        last_request = data["normal"]["snapshot"]["requests"][-1]["request_id"]
        moved = [event for event in events if event.get("request_id") == last_request]
        remaining = [event for event in events if event not in moved]
        split = next(index for index, event in enumerate(remaining) if event["kind"] == "tool.pending")
        self.reorder(data, remaining[:split] + moved + remaining[split:])
        with self.assertRaises(AssertionError):
            validate(data)

    def test_turn_completion_waits_for_final_model_response(self):
        data = read_capture(FIXTURES / "behavior.json.gz")
        events = data["normal"]["snapshot"]["events"]
        response = [event for event in events if event["kind"] == "request.completed"][-1]
        events.remove(response)
        events.append(response)
        self.reorder(data, events)
        with self.assertRaises(AssertionError):
            validate(data)

    def test_normalization_rejects_broken_identity_reference(self):
        data = read_capture(FIXTURES / "behavior.json.gz")
        data["normal"]["snapshot"]["tool_calls"][0]["request_id"] = "wrong-request"
        with self.assertRaises(AssertionError):
            normalize(data)

    def test_normalization_rejects_reordered_causal_events(self):
        data = read_capture(FIXTURES / "behavior.json.gz")
        events = data["normal"]["snapshot"]["events"]
        started = next(event for event in events if event["kind"] == "request.started")
        completed = next(event for event in events if event["kind"] == "request.completed")
        # 保持游标与序号有序，仍须识别请求结果先于开始的语义错误。
        first, last = events.index(started), events.index(completed)
        events[first], events[last] = completed, started
        for index, event in enumerate(events, 1):
            event["sequence"] = index
            event["cursor"] = index
        data["normal"]["sse"] = [{"schema_version": 1, **event} for event in events]
        with self.assertRaises(AssertionError):
            validate(data)
