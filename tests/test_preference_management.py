"""通过公开接口验证管理结果、生命周期与迟到的模型响应。"""
import asyncio
import json
import tempfile
import unittest

import test_preferences as support
from test_preferences import Clock, PreferenceModel


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    service = support.PreferenceTests.service
    finish = support.PreferenceTests.finish

    async def test_edit_is_versioned_persistent_and_immediately_used_in_new_conversations(self):
        model, clock = PreferenceModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                original = (await client.get('/api/preferences')).json()['preferences'][0]
                path = '/api/preferences/' + original['id']
                edited = await client.patch(path, json={'version': original['version'], 'content': '喜欢清淡口味'})
                self.assertEqual(edited.status_code, 200)
                saved = edited.json()['preferences'][0]
                self.assertEqual(saved['content'], '喜欢清淡口味')
                self.assertEqual(saved['version'], original['version'] + 1)
                conflict = await client.patch(path, json={'version': original['version'], 'content': '旧页面覆盖'})
                self.assertEqual(conflict.status_code, 409)
                self.assertIn('刷新', conflict.json()['detail']['message'])
                self.assertEqual((await client.delete(path, params={'version': original['version']})).status_code, 409)
                self.assertEqual((await client.patch(path, json={'version': saved['version'], 'content': '  '})).status_code, 422)
                await self.finish(client, '推荐餐馆')
                self.assertIn('喜欢清淡口味', model.requests[-1]['system'])
                self.assertNotIn('不吃辣', model.requests[-1]['system'])
            async with self.service(directory, model, clock) as client:
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [saved])
                await self.finish(client, '重新推荐餐馆')
                self.assertIn('喜欢清淡口味', model.requests[-1]['system'])

    async def test_management_blocks_inflight_and_received_old_inputs_but_allows_new_expression(self):
        for operation in ('edit', 'delete'):
            with self.subTest(operation=operation):
                model, clock = PreferenceModel(), Clock()
                with tempfile.TemporaryDirectory() as directory:
                    async with self.service(directory, model, clock) as client:
                        original_chat = await self.finish(client, '我一直不吃辣')
                        await clock.advance(3600)
                        original = (await client.get('/api/preferences')).json()['preferences'][0]
                        extracting, release = asyncio.Event(), asyncio.Event()
                        create = model.create
                        async def waiting(**kwargs):
                            if kwargs['system'].startswith('旅行者偏好提取'):
                                extracting.set()
                                await release.wait()
                            return await create(**kwargs)
                        model.create = waiting
                        await self.finish(client, '我一直不吃辣')
                        await clock.advance(3600)
                        self.assertTrue(extracting.is_set())
                        # 已接收、仍未闲置到提取时机的另一个会话同样不能恢复旧偏好。
                        await self.finish(client, '我一直不吃辣')
                        path = '/api/preferences/' + original['id']
                        if operation == 'edit':
                            result = await client.patch(path, json={'version': original['version'], 'content': '喜欢清淡口味'})
                            expected = ['喜欢清淡口味']
                        else:
                            result = await client.delete(path, params={'version': original['version']})
                            expected = []
                        self.assertEqual(result.status_code, 200)
                        release.set()
                        for _ in range(30):
                            await asyncio.sleep(0)
                        self.assertEqual([p['content'] for p in (await client.get('/api/preferences')).json()['preferences']], expected)
                    async with self.service(directory, model, clock) as client:
                        await clock.advance(3600)
                        self.assertEqual([p['content'] for p in (await client.get('/api/preferences')).json()['preferences']], expected)
                        model.extract = lambda inputs: {'changes': []}
                        await self.finish(client, '继续规划', original_chat['session_id'])
                        self.assertIn('我一直不吃辣', json.dumps(model.requests[-1]['messages'], ensure_ascii=False))
                        if operation == 'delete':
                            self.assertNotIn('不吃辣', model.requests[-1]['system'])
                        await clock.advance(3600)
                        self.assertEqual([p['content'] for p in (await client.get('/api/preferences')).json()['preferences']], expected)
                        model.extract = lambda inputs: {'changes': [{'operation': 'add', 'category': 'diet', 'content': '不吃辣',
                            'source_turn_id': inputs[0]['turn_id'], 'evidence': '不吃辣'}]}
                        fresh = await self.finish(client, '我重新明确：一直不吃辣')
                        await clock.advance(3600)
                        saved = (await client.get('/api/preferences')).json()['preferences']
                        self.assertIn(('不吃辣', fresh['turn_id']), [(p['content'], p['source_turn_id']) for p in saved])

    async def test_chat_deletion_keeps_preferences_manageable_and_preference_deletion_survives_restart(self):
        model, clock = PreferenceModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                original = (await client.get('/api/preferences')).json()['preferences'][0]
                self.assertEqual((await client.delete('/api/sessions/' + first['session_id'])).status_code, 200)
                detached = (await client.get('/api/preferences')).json()['preferences'][0]
                self.assertEqual(detached['content'], '不吃辣')
                self.assertIsNone(detached['source_turn_id'])
                path = '/api/preferences/' + detached['id']
                edit = await client.patch(path, json={'version': original['version'], 'content': '素食'})
                self.assertEqual(edit.status_code, 200)
                saved = edit.json()['preferences'][0]
                self.assertEqual((await client.delete(path, params={'version': saved['version']})).status_code, 200)
                self.assertEqual((await client.delete(path, params={'version': saved['version']})).status_code, 404)
                self.assertEqual((await client.patch(path, json={'version': saved['version'], 'content': '恢复'})).status_code, 404)
            async with self.service(directory, model, clock) as client:
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [])
                await self.finish(client, '推荐餐厅')
                self.assertNotIn('素食', model.requests[-1]['system'])
                self.assertNotIn('不吃辣', model.requests[-1]['system'])

    async def test_diet_management_keeps_unrelated_pending_activity_expression(self):
        model, clock = PreferenceModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                original = (await client.get('/api/preferences')).json()['preferences'][0]
                entered, release = asyncio.Event(), asyncio.Event()
                create = model.create
                async def waiting(**kwargs):
                    if kwargs['system'].startswith('旅行者偏好提取'):
                        entered.set()
                        await release.wait()
                    return await create(**kwargs)
                model.create = waiting
                model.extract = lambda inputs: {'changes': [
                    {'operation': 'add', 'category': 'diet', 'content': '不吃辣', 'source_turn_id': inputs[0]['turn_id'], 'evidence': '不吃辣'},
                    {'operation': 'add', 'category': 'activity', 'content': '喜欢博物馆', 'source_turn_id': inputs[0]['turn_id'], 'evidence': '喜欢博物馆'}]}
                await self.finish(client, '我一直不吃辣，也一直喜欢博物馆')
                await clock.advance(3600)
                self.assertTrue(entered.is_set())
                pending = await self.finish(client, '我一直不吃辣，也一直喜欢博物馆')
                deleted = await client.delete('/api/preferences/' + original['id'], params={'version': original['version']})
                self.assertEqual(deleted.status_code, 200)
                release.set()
                for _ in range(30):
                    await asyncio.sleep(0)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual([(p['category'], p['content']) for p in saved], [('activity', '喜欢博物馆')])
                await clock.advance(3600)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual([(p['category'], p['content']) for p in saved], [('activity', '喜欢博物馆')])
                extracts = [r for r in model.requests if r['system'].startswith('旅行者偏好提取')]
                self.assertIn(pending['turn_id'], [i['turn_id'] for i in json.loads(extracts[-1]['messages'][0]['content'])['new_inputs']])

    async def test_manual_edit_resolves_saved_ambiguity_persistently(self):
        from test_preference_changes import ChangesModel, add, targeted
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload)]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                original = (await client.get('/api/preferences')).json()['preferences'][0]
                model.suggest = lambda payload: {'changes': [targeted(payload, 'ambiguity', '是否接受辣味', '那个现在也可以了')]}
                await self.finish(client, '那个现在也可以了')
                await clock.advance(3600)
                self.assertEqual(len((await client.get('/api/preferences')).json()['ambiguities']), 1)
                edited = await client.patch('/api/preferences/' + original['id'], json={'version': original['version'], 'content': '喜欢清淡口味'})
                self.assertEqual(edited.status_code, 200)
                self.assertEqual(edited.json()['ambiguities'], [])
            async with self.service(directory, model, clock) as client:
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['ambiguities'], [])
                self.assertEqual([p['content'] for p in state['preferences']], ['喜欢清淡口味'])

    async def test_late_targeted_update_and_ambiguity_cannot_undo_management(self):
        from test_preference_changes import ChangesModel, add, targeted
        for management in ('edit', 'delete'):
            for operation in ('update', 'ambiguity'):
                with self.subTest(management=management, operation=operation):
                    model, clock = ChangesModel(), Clock()
                    model.suggest = lambda payload: {'changes': [add(payload)]}
                    with tempfile.TemporaryDirectory() as directory:
                        async with self.service(directory, model, clock) as client:
                            await self.finish(client, '我一直不吃辣')
                            await clock.advance(3600)
                            original = (await client.get('/api/preferences')).json()['preferences'][0]
                            entered, release = asyncio.Event(), asyncio.Event()
                            create = model.create
                            async def waiting(**kwargs):
                                if kwargs['system'].startswith('旅行者偏好提取'):
                                    entered.set()
                                    await release.wait()
                                return await create(**kwargs)
                            model.create = waiting
                            expression = '我以后喜欢吃辣' if operation == 'update' else '那个现在也可以了'
                            model.suggest = lambda payload: {'changes': [targeted(payload, operation, '喜欢吃辣', expression)]}
                            await self.finish(client, expression)
                            await clock.advance(3600)
                            self.assertTrue(entered.is_set())
                            path = '/api/preferences/' + original['id']
                            if management == 'edit':
                                managed = await client.patch(path, json={'version': original['version'], 'content': '喜欢清淡口味'})
                            else:
                                managed = await client.delete(path, params={'version': original['version']})
                            self.assertEqual(managed.status_code, 200)
                            saved = managed.json()['preferences']
                            release.set()
                            for _ in range(30):
                                await asyncio.sleep(0)
                            state = (await client.get('/api/preferences')).json()
                            self.assertEqual(state['preferences'], saved)
                            self.assertEqual(state['ambiguities'], [])
                        async with self.service(directory, model, clock) as client:
                            state = (await client.get('/api/preferences')).json()
                            self.assertEqual(state['preferences'], saved)
                            self.assertEqual(state['ambiguities'], [])
