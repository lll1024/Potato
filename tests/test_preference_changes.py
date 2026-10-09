"""公开接口上的变更协议验收；替身只证明协议，不证明模型语义。"""
import json
import tempfile
import unittest

from test_agent import response
import test_preferences as helpers
from test_preferences import Clock, PreferenceModel


class ChangesModel(PreferenceModel):
    def __init__(self):
        super().__init__()
        self.suggest = lambda payload: {'changes': []}

    async def create(self, **kwargs):
        if kwargs['system'].startswith('旅行者偏好提取'):
            self.requests.append(kwargs)
            payload = json.loads(kwargs['messages'][0]['content'])
            return response([{'type': 'text', 'text': json.dumps(self.suggest(payload), ensure_ascii=False)}])
        return await super().create(**kwargs)


def add(payload, content='不吃辣', evidence='不吃辣', index=0):
    return {'operation': 'add', 'category': 'diet', 'content': content,
            'source_turn_id': payload['new_inputs'][index]['turn_id'], 'evidence': evidence}


def targeted(payload, operation, content, evidence, index=0):
    target = payload['existing_preferences'][0]
    return {**add(payload, content, evidence, index), 'operation': operation,
            'target_id': target['id'], 'target_version': target['version']}


class PreferenceChangesTests(unittest.IsolatedAsyncioTestCase):
    service = helpers.PreferenceTests.service
    finish = helpers.PreferenceTests.finish
    async def test_duplicate_expression_keeps_one_record_and_latest_source_order(self):
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload)]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                before = (await client.get('/api/preferences')).json()['preferences'][0]
                latest = await self.finish(client, '我仍然不吃辣')
                await clock.advance(3600)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual([(p['id'], p['content'], p['source_turn_id']) for p in saved],
                                 [(before['id'], '不吃辣', latest['turn_id'])])
                self.assertGreater(saved[0]['input_order'], before['input_order'])
                self.assertGreater(saved[0]['version'], before['version'])

    async def test_temporary_exception_keeps_value_and_explicit_long_term_change_updates_it(self):
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload)]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                before = (await client.get('/api/preferences')).json()['preferences'][0]
                model.suggest = lambda payload: {'changes': []}
                await self.finish(client, '这次可以吃一点辣')
                self.assertEqual(model.requests[-1]['messages'][-1]['content'], '这次可以吃一点辣')
                await clock.advance(3600)
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [before])
                model.suggest = lambda payload: {'changes': [targeted(payload, 'update', '喜欢吃辣', '现在喜欢吃辣') ]}
                latest = await self.finish(client, '我现在喜欢吃辣了，以后也按这个口味推荐')
                await clock.advance(3600)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual([(p['id'], p['content'], p['source_turn_id']) for p in saved],
                                 [(before['id'], '喜欢吃辣', latest['turn_id'])])
                self.assertGreater(saved[0]['updated_at'], before['updated_at'])
                await self.finish(client, '推荐餐馆')
                self.assertIn('喜欢吃辣', model.requests[-1]['system'])
                self.assertNotIn('不吃辣', model.requests[-1]['system'])

    async def test_same_batch_changes_follow_user_order_even_when_suggestions_are_reversed(self):
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload)]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                first = await self.finish(client, '我以后喜欢微辣')
                latest = await self.finish(client, '我说错了，我长期喜欢重辣', first['session_id'])
                model.suggest = lambda payload: {'changes': [
                    targeted(payload, 'update', '喜欢重辣', '长期喜欢重辣', 1),
                    targeted(payload, 'update', '喜欢微辣', '以后喜欢微辣', 0)]}
                await clock.advance(3600)
                saved = (await client.get('/api/preferences')).json()['preferences']
                self.assertEqual([(p['content'], p['source_turn_id']) for p in saved], [('喜欢重辣', latest['turn_id'])])

    async def test_ambiguous_change_preserves_effective_preference_and_tracks_new_evidence(self):
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload)]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                before = (await client.get('/api/preferences')).json()['preferences'][0]
                model.suggest = lambda payload: {'changes': [targeted(payload, 'ambiguity', '是否长期接受辣味', '那个现在也可以了')]}
                latest = await self.finish(client, '那个现在也可以了', first['session_id'])
                answer = (await client.get('/api/sessions/' + first['session_id'])).json()['turns'][-1]['answer']
                await clock.advance(3600)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['preferences'], [before])
                pending = state['ambiguities'][0]
                self.assertEqual((pending['target_id'], pending['source_turn_id'], pending['evidence']),
                                 (before['id'], latest['turn_id'], '那个现在也可以了'))
                self.assertEqual((await client.get('/api/sessions/' + first['session_id'])).json()['turns'][-1]['answer'], answer)
            async with self.service(directory, model, clock) as client:
                self.assertEqual((await client.get('/api/preferences')).json()['ambiguities'], state['ambiguities'])

    async def test_older_session_late_extraction_cannot_override_newer_duplicate_expression(self):
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload)]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                older = await self.finish(client, '我以后喜欢吃辣')
                await clock.advance(10)
                latest = await self.finish(client, '我仍然不吃辣')
                await clock.advance(10)
                await self.finish(client, '继续规划', older['session_id'])
                model.suggest = lambda payload: {'changes': [
                    targeted(payload, 'update', '喜欢吃辣', '以后喜欢吃辣') if payload['new_inputs'][0]['input'] == '我以后喜欢吃辣'
                    else add(payload)]}
                await clock.advance(3590)
                saved = (await client.get('/api/preferences')).json()['preferences'][0]
                self.assertEqual(saved['source_turn_id'], latest['turn_id'])
                await clock.advance(10)
                self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [saved])
                await self.finish(client, '推荐餐馆')
                self.assertIn('不吃辣', model.requests[-1]['system'])
                self.assertNotIn('喜欢吃辣', model.requests[-1]['system'])

    async def test_context_explains_reference_but_cannot_supply_new_source(self):
        model, clock = ChangesModel(), Clock()
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                first = await self.finish(client, '我长期喜欢吃辣')
                await clock.advance(3600)
                await self.finish(client, '那个一直如此', first['session_id'])
                captured = []
                def suggest(payload):
                    captured.append(payload)
                    return {'changes': [{**add(payload, '喜欢吃辣', '长期喜欢吃辣'), 'source_turn_id': first['turn_id']}]}
                model.suggest = suggest
                await clock.advance(3600)
                self.assertEqual(captured[0]['context'][0], {'role': 'user', 'content': '我长期喜欢吃辣'})
                self.assertEqual([item['input'] for item in captured[0]['new_inputs']], ['那个一直如此'])
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['preferences'], [])
                self.assertEqual(state['processing'][0]['status'], 'failed')

    async def test_invalid_update_target_rejects_whole_batch_without_partial_add(self):
        for invalid in ({'target_id': 'unknown'}, {'target_version': True}, {'target_version': 99}, {'category': 'transport'}):
            with self.subTest(invalid=invalid):
                model, clock = ChangesModel(), Clock()
                model.suggest = lambda payload: {'changes': [add(payload)]}
                with tempfile.TemporaryDirectory() as directory:
                    async with self.service(directory, model, clock) as client:
                        await self.finish(client, '我一直不吃辣')
                        await clock.advance(3600)
                        saved = (await client.get('/api/preferences')).json()['preferences']
                        await self.finish(client, '我以后喜欢吃辣，也长期喜欢素食')
                        model.suggest = lambda payload: {'changes': [add(payload, '喜欢素食', '长期喜欢素食'),
                            {**targeted(payload, 'update', '喜欢吃辣', '以后喜欢吃辣'), **invalid}]}
                        await clock.advance(3600)
                        state = (await client.get('/api/preferences')).json()
                        self.assertEqual(state['preferences'], saved)
                        self.assertEqual(state['processing'][0]['status'], 'failed')

    async def test_late_old_change_cannot_undo_newer_unresolved_expression(self):
        model, clock = ChangesModel(), Clock()
        model.suggest = lambda payload: {'changes': [add(payload)]}
        with tempfile.TemporaryDirectory() as directory:
            async with self.service(directory, model, clock) as client:
                await self.finish(client, '我一直不吃辣')
                await clock.advance(3600)
                before = (await client.get('/api/preferences')).json()['preferences']
                older = await self.finish(client, '我以后喜欢吃辣')
                await clock.advance(10)
                await self.finish(client, '那个现在也可以了')
                await clock.advance(10)
                await self.finish(client, '继续规划', older['session_id'])
                model.suggest = lambda payload: {'changes': [
                    targeted(payload, 'update', '喜欢吃辣', '以后喜欢吃辣') if payload['new_inputs'][0]['input'] == '我以后喜欢吃辣'
                    else targeted(payload, 'ambiguity', '是否长期接受辣味', '那个现在也可以了')]}
                await clock.advance(3590)
                pending = (await client.get('/api/preferences')).json()['ambiguities']
                await clock.advance(10)
                state = (await client.get('/api/preferences')).json()
                self.assertEqual(state['preferences'], before)
                self.assertEqual(state['ambiguities'], pending)
