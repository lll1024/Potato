"""固定迁移前行为与合成旧数据；测试只经公开 HTTP/SSE 和真实进程。"""
import gzip
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from compatibility.capture import FIXTURES, ROOT, ScenarioServices, capture, service, snapshot, submit
from compatibility.compare import normalize, read_capture, validate


class MigrationBaselineTests(unittest.IsolatedAsyncioTestCase):
    maxDiff = 3000

    async def test_public_web_sse_cli_and_process_behaviors_match_fixed_baseline(self):
        baseline = read_capture(FIXTURES / "behavior.json.gz")
        with tempfile.TemporaryDirectory() as directory:
            actual = await capture(ROOT, Path(directory))
        expected, observed = normalize(baseline), normalize(actual)
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
