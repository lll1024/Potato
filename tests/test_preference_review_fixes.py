"""公开 HTTP 验证审查修复，替身只证明协议边界。"""
import json
import tempfile
import unittest

from test_preference_changes import ChangesModel, add
from test_preferences import Clock
import test_preferences as support


class ReviewFixTests(unittest.IsolatedAsyncioTestCase):
    def service(self, directory, model, clock):
        return support.PreferenceTests().service(directory, model, clock)
    finish = support.PreferenceTests.finish

    async def test_manual_edit_redacts_credentials_in_response_background_and_restart(self):
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload)]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                original = (await client.get('/api/preferences')).json()['preferences'][0]
                token = 'REVIEW-FAKE-CREDENTIAL-ONLY'
                edited = await client.patch('/api/preferences/' + original['id'], json={
                    'version': original['version'], 'content': '喜欢清淡 password: ' + token})
                self.assertEqual(edited.status_code, 200)
                self.assertNotIn(token, edited.text)
                self.assertNotIn(token, (await client.get('/api/preferences')).text)
                await self.finish(client, '以后怎么选餐馆')
                self.assertNotIn(token, model.requests[-1]['system'])
                self.assertIn('喜欢清淡', model.requests[-1]['system'])
            async with self.service(directory, model, clock) as client:
                self.assertNotIn(token, (await client.get('/api/preferences')).text)
                await self.finish(client, '以后怎么选餐馆')
                self.assertNotIn(token, model.requests[-1]['system'])

    async def test_management_keeps_unrelated_same_category_inputs_across_restart(self):
        for operation in ('edit', 'delete'):
            with self.subTest(operation=operation):
                model, clock = ChangesModel(), Clock()
                model.suggest = lambda payload: {'changes': [add(payload)]}
                with tempfile.TemporaryDirectory() as directory:
                    async with self.service(directory, model, clock) as client:
                        await self.finish(client, '我一直不吃辣')
                        await clock.advance(3600)
                        original = (await client.get('/api/preferences')).json()['preferences'][0]
                        tea = await self.finish(client, '我长期喜欢喝绿茶')
                        path = '/api/preferences/' + original['id']
                        if operation == 'edit':
                            result = await client.patch(path, json={'version': original['version'], 'content': '喜欢清淡口味'})
                            expected = ['喜欢清淡口味', '喜欢喝绿茶']
                        else:
                            result = await client.delete(path, params={'version': original['version']})
                            expected = ['喜欢喝绿茶']
                        self.assertEqual(result.status_code, 200)
                    model.suggest = lambda payload: {'changes': [add(payload, '喜欢喝绿茶', '长期喜欢喝绿茶')]}
                    async with self.service(directory, model, clock) as client:
                        await clock.advance(3600)
                        state = (await client.get('/api/preferences')).json()
                        self.assertCountEqual([p['content'] for p in state['preferences']], expected)
                        self.assertEqual(next(p for p in state['preferences'] if p['content'] == '喜欢喝绿茶')['source_turn_id'], tea['turn_id'])
                        self.assertEqual(state['processing'], [])

    async def test_same_batch_assistant_answer_explains_reference_but_never_authorizes_source(self):
        from test_agent import response
        model, clock = ChangesModel(), Clock()
        create = model.create
        async def answering(**kwargs):
            if kwargs['system'].startswith('旅行者偏好提取'):
                return await create(**kwargs)
            model.requests.append(kwargs)
            return response([{'type': 'text', 'text': '可以考虑自然公园徒步。'}])
        model.create = answering
        seen = []
        def suggest(payload):
            seen.append(payload)
            if '自然公园徒步' not in json.dumps(payload, ensure_ascii=False):
                return {'changes': []}
            return {'changes': [{**add(payload, '长期喜欢自然公园徒步', '你刚才说的那个我一直很喜欢', 1), 'category': 'activity'}]}
        model.suggest = suggest
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '请建议一种户外活动')
                latest = await self.finish(client, '你刚才说的那个我一直很喜欢', first['session_id'])
                await clock.advance(3600)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual([(p['content'], p['source_turn_id']) for p in state['preferences']],
                                 [('长期喜欢自然公园徒步', latest['turn_id'])])
                self.assertIn('自然公园徒步', json.dumps(seen[0], ensure_ascii=False))
                # 让助手回答自己的文字成为 evidence，公开协议应拒绝且不影响已保存条目。
                model.suggest = lambda payload: {'changes': [{**add(payload, '喜欢新建议', '自然公园徒步'), 'category': 'activity'}]}
                await self.finish(client, '继续', first['session_id'])
                await clock.advance(3600)
                await clock.advance(30)
                await clock.advance(120)
                after = (await client.get('/api/preferences')).json()
                self.assertEqual(after['preferences'], state['preferences'])
                self.assertEqual(after['processing'][0]['status'], 'failed')

    async def test_record_protection_reextracts_late_result_and_survives_retry_restart(self):
        import asyncio
        for operation in ('edit', 'delete'):
            for inflight in (False, True):
                with self.subTest(operation=operation, inflight=inflight):
                    model, clock = ChangesModel(), Clock()
                    model.suggest = lambda payload: {'changes': [add(payload)]}
                    seen, failed_once = [], False
                    entered, release = asyncio.Event(), asyncio.Event()
                    create = model.create
                    async def waiting(**kwargs):
                        if kwargs['system'].startswith('旅行者偏好提取') and inflight and not release.is_set():
                            entered.set()
                            await release.wait()
                        return await create(**kwargs)
                    with tempfile.TemporaryDirectory() as directory:
                        async with self.service(directory, model, clock) as client:
                            await self.finish(client, '我一直不吃辣')
                            await clock.advance(3600)
                            original = (await client.get('/api/preferences')).json()['preferences'][0]
                            source = await self.finish(client, '我一向避开辣椒，而且长期喜欢喝绿茶')
                            def suggest(payload):
                                nonlocal failed_once
                                seen.append(payload)
                                if payload['managed_preferences']:
                                    if not failed_once:
                                        failed_once = True
                                        raise RuntimeError('test model unavailable')
                                    guard = payload['managed_preferences'][0]
                                    old = {**add(payload, '避免辣味', '一向避开辣椒'), 'operation': 'update',
                                           'target_id': guard['id'], 'target_version': guard['version']}
                                else:
                                    old = add(payload, '避免辣味', '一向避开辣椒')
                                return {'changes': [old, add(payload, '喜欢喝绿茶', '长期喜欢喝绿茶')]}
                            model.suggest = suggest
                            model.create = waiting
                            if inflight:
                                await clock.advance(3600)
                                self.assertTrue(entered.is_set())
                            path = '/api/preferences/' + original['id']
                            managed = (await client.patch(path, json={'version': original['version'], 'content': '喜欢清淡口味'})
                                       if operation == 'edit' else await client.delete(path, params={'version': original['version']}))
                            self.assertEqual(managed.status_code, 200)
                            expected = managed.json()['preferences']
                            release.set()
                            if not inflight:
                                await clock.advance(3600)
                            for _ in range(60):
                                await asyncio.sleep(0)
                            state = (await client.get('/api/preferences')).json()
                            self.assertEqual(state['preferences'], expected)
                            self.assertTrue(failed_once)
                            self.assertEqual([(p['status'], p['attempts']) for p in state['processing']], [('pending', 1)])
                        async with self.service(directory, model, clock) as client:
                            await clock.advance(29)
                            self.assertEqual((await client.get('/api/preferences')).json()['preferences'], expected)
                            await clock.advance(1)
                            state = (await client.get('/api/preferences')).json()
                            self.assertCountEqual([p['content'] for p in state['preferences']],
                                                  [p['content'] for p in expected] + ['喜欢喝绿茶'])
                            self.assertEqual(next(p for p in state['preferences'] if p['content'] == '喜欢喝绿茶')['source_turn_id'], source['turn_id'])
                            self.assertEqual(state['processing'], [])
                            self.assertTrue(seen[-1]['managed_preferences'])
                            await self.finish(client, '下一次点餐原则')
                            self.assertNotIn('避免辣味', model.requests[-1]['system'])
                            self.assertNotIn('managed_preferences', model.requests[-1]['system'])
                            model.suggest = lambda payload: {'changes': [add(payload, '避免辣味', '一向避开辣椒')]}
                            latest = await self.finish(client, '我再次明确：我一向避开辣椒')
                            await clock.advance(3600)
                            state = (await client.get('/api/preferences')).json()
                            self.assertIn(('避免辣味', latest['turn_id']), [(p['content'], p['source_turn_id']) for p in state['preferences']])

    async def test_update_to_existing_content_merges_records_and_clears_both_ambiguities(self):
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload, '喜欢喝红茶', '长期喜欢喝红茶'), add(payload, '喜欢喝绿茶', '长期喜欢喝绿茶')]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我长期喜欢喝红茶，也长期喜欢喝绿茶')
                await clock.advance(3600)
                originals = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual(len(originals), 2)
                def ambiguous(payload):
                    return {'changes': [{**add(payload, '茶饮需要澄清', '茶口味现在可以了'), 'operation': 'ambiguity',
                        'target_id': item['id'], 'target_version': item['version']} for item in payload['existing_preferences']]}
                model.suggest = ambiguous
                await self.finish(client, '茶口味现在可以了')
                await clock.advance(3600)
                self.assertEqual(len((await client.get('/api/preferences')).json()['ambiguities']), 2)
                def changing(payload):
                    target = next(p for p in payload['existing_preferences'] if p['content'] == '喜欢喝红茶')
                    return {'changes': [{**add(payload, '喜欢喝绿茶', '以后只喝绿茶'), 'operation': 'update',
                        'target_id': target['id'], 'target_version': target['version']}]}
                model.suggest = changing
                latest = await self.finish(client, '我以后只喝绿茶，长期不喝红茶了')
                await clock.advance(3600)
                saved = (await client.get('/api/preferences')).json()
                self.assertEqual([(p['content'], p['source_turn_id']) for p in saved['preferences']], [('喜欢喝绿茶', latest['turn_id'])])
                self.assertGreater(saved['preferences'][0]['input_order'], max(p['input_order'] for p in originals))
                self.assertEqual(saved['ambiguities'], [])
                await self.finish(client, '以后怎么选茶')
                background = model.requests[-1]['system'].split('旅行者偏好背景数据', 1)[1]
                self.assertEqual(background.count('喜欢喝绿茶'), 1)
            async with self.service(directory, model, clock) as client:
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], saved['preferences'])

    async def test_merge_cannot_replace_newer_duplicate_or_undo_duplicate_management(self):
        for protection in ('newer_source', 'newer_ambiguity', 'managed'):
            with self.subTest(protection=protection):
                model, clock = ChangesModel(), Clock()
                model.suggest = lambda payload: {'changes': [add(payload, '喜欢喝红茶', '长期喜欢喝红茶'), add(payload, '喜欢喝绿茶', '长期喜欢喝绿茶')]}
                with tempfile.TemporaryDirectory() as directory:
                    async with self.service(directory, model, clock) as client:
                        await self.finish(client, '我长期喜欢喝红茶，也长期喜欢喝绿茶')
                        await clock.advance(3600)
                        old = await self.finish(client, '我以后只喝绿茶')
                        await clock.advance(10)
                        new = await self.finish(client, '我仍然长期喜欢喝绿茶')
                        await clock.advance(10)
                        await self.finish(client, '继续', old['session_id'])
                        def suggest(payload):
                            if payload['new_inputs'][0]['turn_id'] == old['turn_id']:
                                target = next(p for p in payload['existing_preferences'] if p['content'] == '喜欢喝红茶')
                                return {'changes': [{**add(payload, '喜欢喝绿茶', '以后只喝绿茶'), 'operation': 'update',
                                    'target_id': target['id'], 'target_version': target['version']}]}
                            if protection == 'newer_ambiguity':
                                target = next(p for p in payload['existing_preferences'] if p['content'] == '喜欢喝绿茶')
                                return {'changes': [{**add(payload, '茶饮需要澄清', '长期喜欢喝绿茶'), 'operation': 'ambiguity',
                                    'target_id': target['id'], 'target_version': target['version']}]}
                            return {'changes': [add(payload, '喜欢喝绿茶', '长期喜欢喝绿茶')]}
                        model.suggest = suggest
                        if protection == 'managed':
                            green = next(p for p in (await client.get('/api/preferences')).json()['preferences'] if p['content'] == '喜欢喝绿茶')
                            self.assertEqual((await client.patch('/api/preferences/' + green['id'], json={
                                'version': green['version'], 'content': '喜欢喝绿茶'})).status_code, 200)
                        await clock.advance(3590)
                        protected = (await client.get('/api/preferences')).json()
                        if protection == 'newer_source':
                            self.assertEqual(next(p for p in protected['preferences'] if p['content'] == '喜欢喝绿茶')['source_turn_id'], new['turn_id'])
                        await clock.advance(10)
                        after = (await client.get('/api/preferences')).json()
                        expected = protected['preferences'] if protection == 'managed' else [p for p in protected['preferences'] if p['content'] == '喜欢喝绿茶']
                        self.assertEqual(after['preferences'], expected)
                        self.assertEqual(after['ambiguities'], protected['ambiguities'])

    async def test_same_batch_later_update_resolves_either_merged_identity(self):
        for later_target in ('喜欢喝红茶', '喜欢喝绿茶'):
            with self.subTest(later_target=later_target):
                model, clock = ChangesModel(), Clock()
                model.suggest = lambda payload: {'changes': [add(payload, '喜欢喝红茶', '长期喜欢喝红茶'), add(payload, '喜欢喝绿茶', '长期喜欢喝绿茶')]}
                with tempfile.TemporaryDirectory() as directory:
                    async with self.service(directory, model, clock) as client:
                        await self.finish(client, '我长期喜欢喝红茶，也长期喜欢喝绿茶')
                        await clock.advance(3600)
                        first = await self.finish(client, '我以后只喝绿茶')
                        latest = await self.finish(client, '我又改了，长期只喝花茶', first['session_id'])
                        def suggest(payload):
                            targets = {p['content']: p for p in payload['existing_preferences']}
                            def update(target_name, content, evidence, index):
                                target = targets[target_name]
                                return {**add(payload, content, evidence, index), 'operation': 'update',
                                    'target_id': target['id'], 'target_version': target['version']}
                            return {'changes': [update(later_target, '喜欢喝花茶', '长期只喝花茶', 1),
                                                update('喜欢喝红茶', '喜欢喝绿茶', '以后只喝绿茶', 0)]}
                        model.suggest = suggest
                        await clock.advance(3600)
                        state = (await client.get('/api/preferences')).json()
                        self.assertEqual([(p['content'], p['source_turn_id']) for p in state['preferences']], [('喜欢喝花茶', latest['turn_id'])])
                        self.assertEqual(state['ambiguities'], [])
                        self.assertEqual(state['processing'], [])
