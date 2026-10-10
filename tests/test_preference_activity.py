"""公开 HTTP 验证恢复聊天与停止对后台偏好资格的影响。"""
import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from typing import cast

import httpx
from anthropic import AnthropicError, AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, response
from test_preferences import Clock, PreferenceModel
import test_preferences as preference_helpers
from web import Runtime, create_app


class ActivityModel(PreferenceModel):
    def __init__(self):
        super().__init__()
        self.main_input = None
        self.main_entered = asyncio.Event()
        self.main_release = asyncio.Event()
        self.block_first_extraction = False
        self.first_extraction_result = 'valid'
        self.extraction_entered = asyncio.Event()
        self.extraction_release = asyncio.Event()
        self.extractions = []
        self.extract = lambda inputs: {'changes': [
            {'operation': 'add', 'category': category, 'content': content,
             'source_turn_id': item['turn_id'], 'evidence': content}
            for item in inputs
            for category, content in [('diet', '不吃辣'), ('transport', '喜欢坐火车'), ('lodging', '喜欢安静房间')]
            if content in item['input']]}

    async def create(self, **kwargs):
        if kwargs['system'].startswith('旅行者偏好提取'):
            self.extractions.append(json.loads(kwargs['messages'][0]['content']))
            if self.block_first_extraction and len(self.extractions) == 1:
                self.extraction_entered.set()
                await self.extraction_release.wait()
                if self.first_extraction_result == 'failure':
                    raise AnthropicError('外部模型暂时失败')
                if self.first_extraction_result == 'invalid':
                    return response([{'type': 'text', 'text': '无效 JSON'}])
        elif kwargs['messages'][-1]['content'] == self.main_input:
            self.main_entered.set()
            await self.main_release.wait()
        return await super().create(**kwargs)


class PreferenceActivityTests(unittest.IsolatedAsyncioTestCase):
    service = preference_helpers.PreferenceTests.service
    finish = preference_helpers.PreferenceTests.finish

    async def settle(self):
        for _ in range(30):
            await asyncio.sleep(0)

    async def test_stop_only_cancels_current_input_and_preserves_earlier_and_other_sessions(self):
        model, clock = ActivityModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '我一直不吃辣')
                other = await self.finish(client, '我一直喜欢安静房间')
                model.main_input = '我喜欢坐火车'
                stopped = (await client.post('/api/turns', json={
                    'input': model.main_input, 'session_id': first['session_id']})).json()
                await asyncio.wait_for(model.main_entered.wait(), 1)
                try:
                    result = await client.post('/api/turns/' + stopped['turn_id'] + '/stop')
                    stopping_requested = result.json()['stopping']
                    await clock.advance(3600)
                    stopping = (await client.get('/api/state')).json()['stopping']
                    while_stopping = (await client.get('/api/preferences')).json()['preferences']
                finally:
                    model.main_release.set()
                await self.settle()
                await clock.advance(3600)
                state = (await client.get('/api/preferences')).json()
            async with self.service(directory, model, clock) as client:
                await clock.advance(3600)
                restarted = (await client.get('/api/preferences')).json()
        self.assertTrue(stopping_requested)
        self.assertTrue(stopping)
        self.assertEqual([(p['content'], p['source_turn_id']) for p in while_stopping],
                         [('喜欢安静房间', other['turn_id'])])
        self.assertCountEqual([(p['content'], p['source_turn_id']) for p in state['preferences']],
                              [('不吃辣', first['turn_id']), ('喜欢安静房间', other['turn_id'])])
        self.assertEqual([(p['turn_id'], p['status']) for p in state['processing']],
                         [(stopped['turn_id'], 'cancelled')])
        self.assertCountEqual([p['content'] for p in restarted['preferences']], ['不吃辣', '喜欢安静房间'])
        self.assertEqual(restarted['processing'][0]['status'], 'cancelled')

    async def test_new_input_invalidates_old_result_without_losing_inputs_even_when_old_model_fails(self):
        for ending in ('valid', 'invalid', 'failure'):
            with self.subTest(ending=ending):
                model, clock = ActivityModel(), Clock()
                model.block_first_extraction = True
                model.first_extraction_result = ending
                with tempfile.TemporaryDirectory() as directory:
                    async with self.service(directory, model, clock) as client:
                        first = await self.finish(client, '我一直不吃辣')
                        await clock.advance(3600)
                        await asyncio.wait_for(model.extraction_entered.wait(), 1)
                        try:
                            second = await self.finish(client, '我喜欢坐火车', first['session_id'])
                            before_release = (await client.get('/api/preferences')).json()
                        finally:
                            model.extraction_release.set()
                        await self.settle()
                        after_release = (await client.get('/api/preferences')).json()
                        await clock.advance(3599)
                        before_hour = (await client.get('/api/preferences')).json()
                        early_requests = len(model.extractions)
                        await clock.advance(1)
                        after_hour = (await client.get('/api/preferences')).json()
                        await clock.advance(3600)
                        later = (await client.get('/api/preferences')).json()
                self.assertEqual(before_release['preferences'], [])
                self.assertEqual(after_release['preferences'], [])
                self.assertEqual([(p['turn_id'], p['status']) for p in after_release['processing']],
                                 [(first['turn_id'], 'pending'), (second['turn_id'], 'pending')])
                self.assertEqual(before_hour['preferences'], [])
                self.assertEqual(early_requests, 1)
                self.assertCountEqual([(p['content'], p['source_turn_id']) for p in after_hour['preferences']],
                                      [('不吃辣', first['turn_id']), ('喜欢坐火车', second['turn_id'])])
                self.assertEqual(after_hour['processing'], [])
                self.assertEqual([i['turn_id'] for i in model.extractions[-1]['new_inputs']],
                                 [first['turn_id'], second['turn_id']])
                self.assertEqual(later['preferences'], after_hour['preferences'])
                self.assertEqual(len(model.extractions), 2)

    async def test_active_conversation_is_skipped_while_another_eligible_session_saves(self):
        model, clock = ActivityModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '我一直不吃辣')
                other = await self.finish(client, '我一直喜欢安静房间')
                model.main_input = '我喜欢坐火车'
                accepted = (await client.post('/api/turns', json={
                    'input': model.main_input, 'session_id': first['session_id']})).json()
                await asyncio.wait_for(model.main_entered.wait(), 1)
                try:
                    await clock.advance(4000)
                    running = (await client.get('/api/state')).json()
                    while_running = (await client.get('/api/preferences')).json()['preferences']
                finally:
                    model.main_release.set()
                await self.settle()
                await clock.advance(3599)
                before_hour = (await client.get('/api/preferences')).json()['preferences']
                await clock.advance(1)
                saved = (await client.get('/api/preferences')).json()['preferences']
        self.assertEqual(running['active_turn_id'], accepted['turn_id'])
        self.assertEqual([(p['content'], p['source_turn_id']) for p in while_running],
                         [('喜欢安静房间', other['turn_id'])])
        self.assertEqual(before_hour, while_running)
        self.assertCountEqual([(p['content'], p['source_turn_id']) for p in saved],
                              [('不吃辣', first['turn_id']), ('喜欢坐火车', accepted['turn_id']),
                               ('喜欢安静房间', other['turn_id'])])

    async def test_reading_and_browser_disconnect_leave_service_processing_after_idle_hour(self):
        model, clock = ActivityModel(), Clock()
        @asynccontextmanager
        async def resources():
            tools = AmapTools(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, 'test-model')
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, resources=resources, preference_clock=clock, preference_wait=clock.wait)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as browser:
                    first = await self.finish(browser, '我一直不吃辣')
                    await clock.advance(3599)
                    history = await browser.get('/api/sessions/' + first['session_id'])
                    before_close = (await browser.get('/api/preferences')).json()['preferences']
                await clock.advance(1)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as reopened:
                    saved = (await reopened.get('/api/preferences')).json()['preferences']
        self.assertEqual(history.status_code, 200)
        self.assertEqual(before_close, [])
        self.assertEqual([(p['content'], p['source_turn_id']) for p in saved], [('不吃辣', first['turn_id'])])
