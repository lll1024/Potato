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


def streaming_projection(actual):
    """只剥离 #98 明确新增的作答事实；固定旧基线及通用 normalize 保持原样。"""
    validate(actual)
    data = copy.deepcopy(actual)
    expected_streams = {}
    expected_queries = {}
    phase_rule = "\n资料获取阶段：仅按需查询；资料齐全时简短确认，勿作答。"
    for scenario in data.values():
        if not isinstance(scenario, dict) or "snapshot" not in scenario:
            continue
        saved, payloads = scenario["snapshot"], scenario["payloads"]
        answers = [r for r in saved["requests"] if r["purpose"] == "travel_answer"]
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
            if event["kind"] == "request.started":
                assert event["data"].pop("answer_phase", "query") == "query"
            kept_events.append(event)
        assert checked_steps == Counter({step: 1 for step in steps})
        for request in saved["requests"]:
            phase = request.pop("answer_phase", None)
            parameters = payloads[request["input_payload_id"]]["content"]
            if phase == "query":
                expected_queries[request["request_id"]] = copy.deepcopy(parameters)
                assert parameters["system"].count(phase_rule) == 1
                parameters["system"] = parameters["system"].replace(phase_rule, "", 1)
            else:
                assert phase_rule not in parameters["system"]
        saved["events"] = kept_events
        for index, event in enumerate(saved["events"], 1):
            event["sequence"] = index
            if event["kind"] in ("context.checked", "context.organized", "context.adopted"):
                # 固定 test-model 使用 conservative_unicode；新增查询规则恰为 60 tokens。
                assert event["data"].get("method", "conservative_unicode") == "conservative_unicode"
                for field in ("estimated_tokens", "raw_estimated_tokens", "before_tokens", "after_tokens"):
                    if field in event["data"]:
                        event["data"][field] -= 60
        scenario["sse"] = [{"schema_version": 1, **e} for e in saved["events"]]
    def serialized(parameters):
        return json.dumps(parameters, sort_keys=True, ensure_ascii=False)
    external_streams = [{k: v for k, v in r.items() if k != "stream"} for r in data["model_requests"] if r.get("stream")]
    assert Counter(map(serialized, external_streams)) == Counter(map(serialized, expected_streams.values()))
    data["model_requests"] = [r for r in data["model_requests"] if not r.get("stream")]
    data["stopping"].pop("instance_id", None)

    queries = Counter(map(serialized, expected_queries.values()))
    for request in data["model_requests"]:
        key = serialized(request)
        if queries[key]:
            queries[key] -= 1
            request["system"] = request["system"].replace(phase_rule, "", 1)
        else:
            assert phase_rule not in request["system"]
    assert not any(queries.values())
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
        unsafe = copy.deepcopy(actual)
        scenario = unsafe["normal"]
        answer_step = next(r["step"] for r in scenario["snapshot"]["requests"] if r["purpose"] == "travel_answer")
        for event in scenario["snapshot"]["events"]:
            if event["kind"] == "context.checked" and event["data"]["step"] == answer_step:
                event["data"]["allowed"] = False
                event["data"]["estimated_tokens"] = event["data"]["input_budget"] + 1
        scenario["sse"] = [{"schema_version": 1, **e} for e in scenario["snapshot"]["events"]]
        with self.assertRaises(AssertionError):
            streaming_projection(unsafe)
        missing = copy.deepcopy(actual)
        scenario = missing["normal"]
        scenario["snapshot"]["events"] = [e for e in scenario["snapshot"]["events"]
            if not (e["kind"] == "context.checked" and e["data"]["step"] == answer_step)]
        for index, event in enumerate(scenario["snapshot"]["events"], 1):
            event["sequence"] = index
        scenario["sse"] = [{"schema_version": 1, **e} for e in scenario["snapshot"]["events"]]
        with self.assertRaises(AssertionError):
            streaming_projection(missing)
        polluted = copy.deepcopy(actual)
        summary = next(r for r in polluted["model_requests"] if r["system"].startswith("整理旅行状态"))
        summary["system"] += "\n资料获取阶段：仅按需查询；资料齐全时简短确认，勿作答。"
        with self.assertRaises(AssertionError):
            streaming_projection(polluted)
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
