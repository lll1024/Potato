"""公开 HTTP 验证待澄清背景；模型语义另由实际服务样例验证。"""
import json
import tempfile
import unittest

import test_preferences as helpers
from test_preference_changes import ChangesModel, add, targeted
from test_preferences import Clock


class PreferenceClarificationTests(unittest.IsolatedAsyncioTestCase):
    def service(self, directory, model, clock):
        return helpers.PreferenceTests().service(directory, model, clock)
    finish = helpers.PreferenceTests.finish

    async def prepare(self, client, model, clock):
        model.suggest = lambda payload: {'changes': [add(payload)]}
        first = await self.finish(client, '我一直不吃辣')
        await clock.advance(3600)
        model.suggest = lambda payload: {'changes': [targeted(payload, 'ambiguity', '是否以后长期接受辣味', '那个现在也可以了')]}
        await self.finish(client, '那个现在也可以了', first['session_id'])
        old = (await client.get('/api/sessions/' + first['session_id'])).json()['turns']
        await clock.advance(3600)
        return first, old, (await client.get('/api/preferences')).json()

    async def test_new_turn_gets_pending_question_after_restart_and_clarification_waits_an_hour(self):
        model, clock = ChangesModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first, old, saved = await self.prepare(client, model, clock)
                self.assertEqual((await client.get('/api/sessions/' + first['session_id'])).json()['turns'], old)
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '杭州东站怎么坐地铁到西湖？')
                unrelated = model.requests[-1]
                self.assertIn('无关需求不要提问', unrelated['system'])
                await self.finish(client, '以后给我推荐餐馆时怎么选口味？')
                related = model.requests[-1]
                self.assertIn('是否以后长期接受辣味', related['system'])
                self.assertIn('那个现在也可以了', related['system'])
                self.assertNotIn('是否以后长期接受辣味', json.dumps(related['messages'], ensure_ascii=False))
                self.assertIn('当前明确要求优先', related['system'])
                await self.finish(client, '这次明确只吃清淡无辣，给我点餐原则')
                self.assertEqual(model.requests[-1]['messages'][-1]['content'], '这次明确只吃清淡无辣，给我点餐原则')
                self.assertIn('是否以后长期接受辣味', model.requests[-1]['system'])
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], saved['preferences'])
                model.suggest = lambda payload: {'changes': [targeted(payload, 'update', '喜欢吃辣', '以后也喜欢吃辣')] if '喜欢吃辣' in payload['new_inputs'][0]['input'] else []}
                clear = await self.finish(client, '刚才我的意思是以后也喜欢吃辣，不只是这次')
                await clock.advance(3599)
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], saved['preferences'])
                await clock.advance(1)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual([(p['content'], p['source_turn_id']) for p in state['preferences']], [('喜欢吃辣', clear['turn_id'])])
                self.assertEqual(state['ambiguities'], [])
                await clock.advance(400 * 86400)
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], state['preferences'])
                await self.finish(client, '这次明确只吃清淡无辣，给我点餐原则')
                self.assertNotIn('是否以后长期接受辣味', model.requests[-1]['system'])
                self.assertEqual((await client.get('/api/sessions/' + first['session_id'])).json()['turns'], old)

    async def test_management_removes_question_from_future_model_inputs_even_after_restart(self):
        for operation in ('edit', 'delete'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                model, clock = ChangesModel(), Clock()
                async with self.service(directory, model, clock) as client:
                    first, old, saved = await self.prepare(client, model, clock)
                    item = saved['preferences'][0]
                    if operation == 'edit':
                        result = await client.patch('/api/preferences/' + item['id'], json={'version': item['version'], 'content': '只接受微辣'})
                    else:
                        result = await client.delete('/api/preferences/' + item['id'], params={'version': item['version']})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()['ambiguities'], [])
                async with self.service(directory, model, clock) as client:
                    await self.finish(client, '以后推荐餐馆怎么选口味？')
                    self.assertNotIn('是否以后长期接受辣味', model.requests[-1]['system'])
                    self.assertNotIn('那个现在也可以了', model.requests[-1]['system'])
                    self.assertEqual((await client.get('/api/sessions/' + first['session_id'])).json()['turns'], old)
