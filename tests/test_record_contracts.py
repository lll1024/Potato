"""本机记录的只读保存材料不携带上下文运行对象。"""
import unittest
import json
import tempfile
from pathlib import Path
from dataclasses import FrozenInstanceError

from travel_app.local_records import ContextCheckpoint, LocalRecords, TurnMaterial


class RecordContractsTests(unittest.TestCase):
    def test_checkpoint_freezes_the_selected_context_before_joint_commit(self):
        state = {"version": 2, "messages": [{"role": "user", "content": "保留西湖酒店"}],
                 "original_watermark": 3}
        checkpoint = ContextCheckpoint.capture(state)
        state["messages"][0]["content"] = "不应影响保存材料"
        self.assertEqual(checkpoint.version, 2)
        self.assertEqual(checkpoint.original_watermark, 3)
        self.assertIn("保留西湖酒店", checkpoint.state_json)
        self.assertNotIn("不应影响保存材料", checkpoint.state_json)
        with self.assertRaises(FrozenInstanceError):
            checkpoint.version = 3


    def test_joint_turn_save_uses_frozen_material_and_hides_database_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            records = LocalRecords(Path(directory))
            try:
                self.assertFalse(hasattr(records, "db"))
                identity = records.accept("查询杭州", submission_id="same-input", fingerprint="input", preference_now=1)
                messages = [{"role": "user", "content": "查询杭州"}, {"role": "assistant", "content": "西湖"}]
                material = TurnMaterial(json.dumps(messages))
                records.finish(identity["session_id"], identity["turn_id"], "西湖", material,
                               {"status": "completed", "reason": None, "answer_source": "model", "tool_error_count": 0},
                               1, preference_now=2)
                snapshot = records.snapshot(identity["session_id"])
                self.assertEqual(snapshot["turns"][0]["status"], "completed")
                self.assertEqual(snapshot["turns"][0]["messages"], messages)
                self.assertEqual(records.preferences.read().data()["inputs"][0]["status"], "pending")
                self.assertEqual(records.submission("same-input")["turn_id"], identity["turn_id"])
                self.assertEqual(json.loads(records.planning_seed(identity["session_id"]).messages_json), messages)
            finally:
                records.close()
