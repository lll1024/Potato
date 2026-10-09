"""通过 HTTP 和生命周期验证旅行者偏好，不绕过后台闲置判定。"""
import asyncio
import copy
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from typing import cast

import httpx
from anthropic import AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, response
from web import Runtime, create_app


class Clock:
    def __init__(self):
        self.now = 10000.0
        self.wakeup = asyncio.Event()

    def __call__(self):
        return self.now

    async def wait(self, delay):
        await self.wakeup.wait()
        self.wakeup.clear()

    async def advance(self, seconds):
        self.now += seconds
        self.wakeup.set()
        for _ in range(20):
            await asyncio.sleep(0)


class PreferenceModel:
    def __init__(self):
        self.messages = self
        self.requests = []
        self.extract = lambda inputs: {'changes': [{'operation': 'add', 'category': 'diet', 'content': '不吃辣',
            'source_turn_id': inputs[0]['turn_id'], 'evidence': '不吃辣'}]}

    async def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        if kwargs['system'].startswith('旅行者偏好提取'):
            inputs = json.loads(kwargs['messages'][0]['content'])['new_inputs']
            return response([{'type': 'text', 'text': json.dumps(self.extract(inputs), ensure_ascii=False)}])
        return response([{'type': 'text', 'text': '可以继续规划。'}])


class PreferenceTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def service(self, directory, model, clock):
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, 'test-model')
        app = create_app(directory, resources=resources, preference_clock=clock, preference_wait=clock.wait)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                yield client

    async def finish(self, client, text, session_id=None):
        accepted = await client.post('/api/turns', json={'input': text, 'session_id': session_id})
        self.assertEqual(accepted.status_code, 202)
        identity = accepted.json()
        for _ in range(100):
            if (await client.get('/api/state')).json()['active_turn_id'] is None:
                return identity
            await asyncio.sleep(0)
        self.fail('对话轮次未结束')

    async def test_preferences_start_empty_then_save_after_one_idle_hour_and_reach_new_conversation(self):
        model, clock = PreferenceModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [])
                first = await self.finish(client, '我一直不吃辣')
                await clock.advance(3599)
                self.assertEqual(len(model.requests), 1)
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [])
                await clock.advance(1)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual([(p['category'], p['content'], p['source_turn_id']) for p in saved], [('diet', '不吃辣', first['turn_id'])])
                await self.finish(client, '推荐餐厅')
                self.assertIn('不吃辣', json.dumps(model.requests[-1]['system'], ensure_ascii=False))
                self.assertNotIn('不吃辣', json.dumps(model.requests[-1]['messages'], ensure_ascii=False))
                snapshot = (await client.get('/api/sessions/' + first['session_id'])).json()
                self.assertEqual(snapshot['turns'][0]['messages'][0]['content'], '我一直不吃辣')

    async def test_new_input_resets_idle_hour_and_active_turns_are_skipped(self):
        model, clock = PreferenceModel(), Clock()
        release = asyncio.Event()
        create = model.create
        async def waiting(**kwargs):
            if kwargs['messages'][-1]['content'] == '我喜欢坐火车':
                await release.wait()
            return await create(**kwargs)
        model.create = waiting
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '我一直不吃辣')
                await clock.advance(3599)
                accepted = await client.post('/api/turns', json={'input': '我喜欢坐火车', 'session_id': first['session_id']})
                self.assertEqual(accepted.status_code, 202)
                await clock.advance(4000)
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [])
                release.set()
                for _ in range(30):
                    await asyncio.sleep(0)
                await clock.advance(3599)
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [])
                await clock.advance(1)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual([p['content'] for p in saved], ['不吃辣'])
                extracts = [r for r in model.requests if r['system'].startswith('旅行者偏好提取')]
                self.assertEqual([i['input'] for i in json.loads(extracts[0]['messages'][0]['content'])['new_inputs']], ['我一直不吃辣', '我喜欢坐火车'])

    async def test_extraction_does_not_block_main_answers_and_saves_only_once(self):
        model, clock = PreferenceModel(), Clock()
        release, extracting = asyncio.Event(), asyncio.Event()
        create = model.create
        async def waiting(**kwargs):
            if kwargs['system'].startswith('旅行者偏好提取'):
                extracting.set()
                await release.wait()
            return await create(**kwargs)
        model.create = waiting
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                self.assertTrue(extracting.is_set())
                await self.finish(client, '现在计划去杭州')
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [])
                release.set()
                for _ in range(30):
                    await asyncio.sleep(0)
                self.assertEqual(len((await client.get('/api/preferences')).json()['preferences']), 1)
                await clock.advance(30)
                extracts = [r for r in model.requests if r['system'].startswith('旅行者偏好提取')]
                self.assertEqual(len(extracts), 1)

    async def test_invalid_source_cannot_create_a_preference_and_failure_is_truthful(self):
        model, clock = PreferenceModel(), Clock()
        model.extract = lambda inputs: {'changes': [{'operation': 'add', 'category': 'diet', 'content': '不吃辣',
            'source_turn_id': inputs[0]['turn_id'], 'evidence': '助手推荐不吃辣'}]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '妈妈不吃辣，我暂时随她吃')
                await clock.advance(3600)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['preferences'], [])
                self.assertEqual(state['processing'][0]['status'], 'failed')
                self.assertEqual(state['processing'][0]['error'], '偏好提取未成功，尚未保存。')
                await self.finish(client, '继续规划')
                self.assertNotIn('不吃辣', json.dumps(model.requests[-1]['system'], ensure_ascii=False))

    async def test_four_categories_are_saved_and_category_outside_scope_is_rejected(self):
        model, clock = PreferenceModel(), Clock()
        expected = [('diet', '不吃辣'), ('activity', '喜欢博物馆'), ('transport', '优先地铁'), ('lodging', '喜欢安静房间')]
        model.extract = lambda inputs: {'changes': [{'operation': 'add', 'category': category, 'content': content,
            'source_turn_id': inputs[0]['turn_id'], 'evidence': content} for category, content in expected]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我长期不吃辣，喜欢博物馆，优先地铁，喜欢安静房间')
                await clock.advance(3600)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertCountEqual([(p['category'], p['content']) for p in saved], expected)
                model.extract = lambda inputs: {'changes': [{'operation': 'add', 'category': 'pace', 'content': '慢慢走',
                    'source_turn_id': inputs[0]['turn_id'], 'evidence': '慢慢走'}]}
                await self.finish(client, '今天很累，慢慢走')
                await clock.advance(3600)
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], saved)

    async def test_incomplete_model_output_cannot_save_and_chat_deletion_keeps_saved_preferences(self):
        model, clock = PreferenceModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                before = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual(len(before), 1)
                self.assertEqual((await client.delete('/api/sessions/' + first['session_id'])).status_code, 200)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual(saved[0]['content'], '不吃辣')
                self.assertIsNone(saved[0]['source_turn_id'])
                create = model.create
                async def truncated(**kwargs):
                    result = await create(**kwargs)
                    if kwargs['system'].startswith('旅行者偏好提取'):
                        result.stop_reason = 'max_tokens'
                    return result
                model.create = truncated
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['preferences'], saved)
                self.assertEqual(state['processing'][0]['status'], 'failed')
            async with self.service(directory, model, clock) as client:
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], saved)
                await self.finish(client, '推荐餐厅')
                self.assertIn('不吃辣', json.dumps(model.requests[-1]['system'], ensure_ascii=False))

    async def test_upgrade_preserves_old_conversation_and_does_not_extract_old_inputs(self):
        from pathlib import Path
        from store import Store
        model, clock = PreferenceModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            # 创建原版本已有的聊天事实；没有启用后的偏好待处理身份。
            old = Store(Path(directory))
            identity = old.accept('我以前喜欢博物馆')
            old.finish(identity['session_id'], identity['turn_id'], '旧回答',
                       [{'role': 'user', 'content': '我以前喜欢博物馆'}, {'role': 'assistant', 'content': '旧回答'}],
                       {'status': 'completed', 'reason': None, 'answer_source': 'model', 'tool_error_count': 0}, 1)
            old.close()
            async with self.service(directory, model, clock) as client:
                await clock.advance(7200)
                snapshot = (await client.get('/api/sessions/' + identity['session_id'])).json()
                self.assertEqual(snapshot['turns'][0]['answer'], '旧回答')
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [])
                self.assertEqual((await client.get('/api/preferences')).json()['processing'], [])
                self.assertEqual(model.requests, [])
