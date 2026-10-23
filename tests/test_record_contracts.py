"""本机记录的只读保存材料不携带上下文运行对象。"""
import unittest
from dataclasses import FrozenInstanceError

from travel_app.local_records import ContextCheckpoint


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
