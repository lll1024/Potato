"""经公开入口验证旅行者偏好和本机记录协作。"""
from pathlib import Path
import tempfile
import unittest

from travel_app.local_records import LocalRecords, PreferenceState, TurnMaterial
from travel_app.preferences import Preferences
from test_preferences import Clock, PreferenceModel


class PreferenceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_lifecycle_saves_after_idle_and_management_updates_background(self):
        with tempfile.TemporaryDirectory() as directory:
            records = LocalRecords(Path(directory))
            clock, model = Clock(), PreferenceModel()
            preferences = Preferences(records, model, 'test-model', redact=lambda text: text,
                                      clock=clock, wait=clock.wait)
            identity = records.accept('我一直不吃辣', preference_now=clock())
            records.finish(identity['session_id'], identity['turn_id'], '可以继续规划。',
                           TurnMaterial('[]'), {'status': 'completed', 'reason': None,
                           'answer_source': 'model', 'tool_error_count': 0}, 1, preference_now=clock())
            preferences.start()
            try:
                await clock.advance(3599)
                self.assertEqual(preferences.snapshot()['preferences'], [])
                await clock.advance(1)
                item = preferences.snapshot()['preferences'][0]
                self.assertEqual((item['content'], item['source_turn_id']), ('不吃辣', identity['turn_id']))
                self.assertIn('不吃辣', preferences.background())
                self.assertEqual(preferences.edit(item['id'], item['version'], '喜欢清淡'), 'saved')
                self.assertIn('喜欢清淡', preferences.background())
                self.assertNotIn('不吃辣', preferences.background())
                self.assertEqual(preferences.delete(item['id'], item['version']), 'conflict')
                self.assertEqual(preferences.delete(item['id'], item['version'] + 1), 'saved')
                self.assertEqual(preferences.snapshot()['preferences'], [])
            finally:
                await preferences.shutdown()
                records.close()

    async def test_record_conditions_reject_late_write_after_new_input_and_management(self):
        with tempfile.TemporaryDirectory() as directory:
            records = LocalRecords(Path(directory))
            clock, model = Clock(), PreferenceModel()
            preferences = Preferences(records, model, 'test-model', redact=lambda text: text,
                                      clock=clock, wait=clock.wait)
            first = records.accept('我一直不吃辣', preference_now=clock())
            records.finish(first['session_id'], first['turn_id'], '好的', TurnMaterial('[]'),
                           {'status': 'completed', 'reason': None, 'answer_source': 'model', 'tool_error_count': 0},
                           1, preference_now=clock())
            preferences.start()
            try:
                await clock.advance(3600)
                saved = preferences.snapshot()['preferences'][0]
                before = records.preferences.read()
                changed = before.data()
                changed['preferences'][0]['content'] = '迟到的旧结果'
                records.accept('现在继续规划', first['session_id'], preference_now=clock())
                self.assertFalse(records.preferences.write(before, PreferenceState.capture(changed)))
                self.assertEqual(preferences.snapshot()['preferences'], [saved])
                before = records.preferences.read()
                changed = before.data()
                changed['preferences'][0]['content'] = '迟到的旧结果'
                self.assertEqual(preferences.edit(saved['id'], saved['version'], '喜欢清淡'), 'saved')
                self.assertFalse(records.preferences.write(before, PreferenceState.capture(changed)))
                self.assertEqual(preferences.snapshot()['preferences'][0]['content'], '喜欢清淡')
            finally:
                await preferences.shutdown()
                records.close()
