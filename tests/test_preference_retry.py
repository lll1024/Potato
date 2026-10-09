"""公开HTTP观察有限重试，不从私有调度函数触发提取。"""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from anthropic import AnthropicError

from test_preferences import Clock, PreferenceModel
import test_preferences as helpers


class PreferenceRetryTests(unittest.IsolatedAsyncioTestCase):
    def service(self, directory, model, clock, **options):
        return helpers.PreferenceTests().service(directory, model, clock, **options)
    finish = helpers.PreferenceTests.finish

    async def test_preference_read_storage_fault_does_not_become_a_finished_model_failure(self):
        model, clock = PreferenceModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                database = sqlite3.connect(Path(directory) / 'travel.sqlite3')
                try:
                    database.execute('DROP TABLE preference_ambiguities')
                    database.commit()
                finally:
                    database.close()
                accepted = await client.post('/api/turns', json={'input': '正常旅行请求'})
                self.assertEqual(accepted.status_code, 202)
                for _ in range(30):
                    await asyncio.sleep(0)
                public = (await client.get('/api/state')).json()
                self.assertFalse(public['accepting'])
                self.assertIn('完整上下文无法读取', public['storage_error'])
                self.assertEqual(public['unsaved_fact']['kind'], 'turn.context')
                session = (await client.get('/api/sessions/' + accepted.json()['session_id'])).json()
                self.assertEqual(session['turns'][0]['status'], 'running')
                self.assertEqual(session['requests'], [])
                self.assertEqual(model.requests, [])
                self.assertEqual((await client.post('/api/turns', json={'input': '继续'})).status_code, 503)

    async def test_blocked_model_is_processing_then_times_out_without_freezing_the_next_session(self):
        model, clock = PreferenceModel(), Clock()
        create = model.create
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        async def blocked(**kwargs):
            if kwargs['system'].startswith('旅行者偏好提取'):
                inputs = json.loads(kwargs['messages'][0]['content'])['new_inputs']
                if '博物馆' in inputs[0]['input']:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
            return await create(**kwargs)
        model.create = blocked
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock, preference_timeout=.1) as client:
                first = await self.finish(client, '我长期喜欢博物馆')
                await self.finish(client, '我长期不吃辣')
                await clock.advance(3600)
                await asyncio.wait_for(entered.wait(), 1)
                running = (await client.get('/api/preferences')).json()['processing']
                self.assertEqual((running[0]['turn_id'], running[0]['status'], running[0]['attempts']),
                                 (first['turn_id'], 'processing', 1))
                await self.finish(client, '普通旅行回答')
                await asyncio.wait_for(cancelled.wait(), 1)
                state = (await client.get('/api/preferences')).json()
                for _ in range(100):
                    state = (await client.get('/api/preferences')).json()
                    if state['preferences']:
                        break
                    await asyncio.sleep(0)
                self.assertEqual([p['content'] for p in state['preferences']], ['不吃辣'])
                self.assertEqual(state['processing'][0]['status'], 'pending')
                for seconds in (30, 120):
                    cancelled.clear()
                    await clock.advance(seconds)
                    await asyncio.wait_for(cancelled.wait(), 1)
                    await asyncio.sleep(.01)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['processing'][0]['status'], 'failed')
                self.assertEqual(state['processing'][0]['attempts'], 3)

    async def test_temporary_model_failure_waits_then_retries_without_blocking_other_sessions(self):
        model, clock = PreferenceModel(), Clock()
        create = model.create
        calls = []
        async def flaky(**kwargs):
            if kwargs['system'].startswith('旅行者偏好提取'):
                inputs = json.loads(kwargs['messages'][0]['content'])['new_inputs']
                calls.append(inputs)
                if len(calls) == 1:
                    raise RuntimeError('external service unavailable')
            return await create(**kwargs)
        model.create = flaky
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '我长期不吃辣')
                await self.finish(client, '我也长期不吃辣')
                await clock.advance(3600)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(len(state['preferences']), 1)
                self.assertEqual([(p['turn_id'], p['status'], p['attempts']) for p in state['processing']],
                                 [(first['turn_id'], 'pending', 1)])
                self.assertEqual(len(calls), 2)
                await self.finish(client, '继续规划')
                await clock.advance(29)
                self.assertEqual(len(calls), 2)
                await clock.advance(1)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['processing'][-1]['attempts'], 0)  # 新主轮次仍等待闲置。
                self.assertNotIn(first['turn_id'], [p['turn_id'] for p in state['processing']])
                self.assertEqual(len(calls), 3)
                self.assertEqual(len(state['preferences']), 1)

    async def test_invalid_output_exhausts_three_attempts_and_does_not_replace_saved_preferences(self):
        model, clock = PreferenceModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我长期不吃辣')
                await clock.advance(3600)
                saved = (await client.get('/api/preferences')).json()['preferences']
                model.extract = lambda inputs: {'changes': [{'operation': 'add', 'category': 'pace', 'content': '慢慢走',
                    'source_turn_id': inputs[0]['turn_id'], 'evidence': '喜欢博物馆'}]}
                await self.finish(client, '长期喜欢博物馆')
                await clock.advance(3600)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['processing'][0]['status'], 'pending')
                self.assertEqual(state['processing'][0]['attempts'], 1)
                await clock.advance(30)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['processing'][0]['status'], 'pending')
                self.assertEqual(state['processing'][0]['attempts'], 2)
                await clock.advance(119)
                self.assertEqual((await client.get('/api/preferences')).json()['processing'][0]['attempts'], 2)
                await clock.advance(1)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['processing'][0]['status'], 'failed')
                self.assertEqual(state['processing'][0]['attempts'], 3)
                self.assertEqual(state['preferences'], saved)
                self.assertEqual(state['processing'][0]['error'], '偏好提取未成功，尚未保存。')
                count = len(model.requests)
                await clock.advance(86400)
                self.assertEqual(len(model.requests), count)
                await self.finish(client, '推荐餐厅')
                self.assertIn('不吃辣', model.requests[-1]['system'])

    async def test_request_errors_have_a_fixed_limit_and_main_failure_does_not_revoke_saved_preferences(self):
        model, clock = PreferenceModel(), Clock()
        create = model.create
        failures = []
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我长期不吃辣')
                await clock.advance(3600)
                saved = (await client.get('/api/preferences')).json()['preferences']
                async def unavailable(**kwargs):
                    if kwargs['system'].startswith('旅行者偏好提取'):
                        failures.append(kwargs)
                        raise AnthropicError('password=external-secret')
                    return await create(**kwargs)
                model.create = unavailable
                await self.finish(client, '我长期喜欢博物馆')
                for seconds in (3600, 30, 120):
                    await clock.advance(seconds)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(len(failures), 3)
                self.assertEqual((state['processing'][0]['status'], state['processing'][0]['attempts']), ('failed', 3))
                self.assertNotIn('external-secret', json.dumps(state))
                self.assertEqual(state['preferences'], saved)
                async def main_failure(**kwargs):
                    raise RuntimeError('external main request failure')
                model.create = main_failure
                identity = await self.finish(client, '普通旅行查询失败')
                session = (await client.get('/api/sessions/' + identity['session_id'])).json()
                self.assertEqual(session['turns'][0]['status'], 'failed')
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], saved)
