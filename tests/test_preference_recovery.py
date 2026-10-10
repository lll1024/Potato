"""真实服务 kill/restart；仅在模型、时钟和SQLite资源边界注入条件。"""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from test_recovery import ExternalServices, process
from test_trace import wait_finished


class PreferenceServices(ExternalServices):
    def __init__(self):
        super().__init__(self.extract)
        self.extract_calls = []
        self.extract_entered = asyncio.Event()
        self.extract_release = asyncio.Event()
        self.response_sent = asyncio.Event()
        self.block_extract = False

    async def extract(self, data):
        payload = json.loads(data['messages'][0]['content'])
        self.extract_calls.append(payload)
        self.extract_entered.set()
        if self.block_extract:
            await self.extract_release.wait()
        changes = []
        for source in payload['new_inputs']:
            for category, content in [('diet', '不吃辣'), ('activity', '喜欢博物馆'), ('transport', '喜欢坐火车')]:
                if content in source['input']:
                    changes.append({'operation': 'add', 'category': category, 'content': content,
                                    'source_turn_id': source['turn_id'], 'evidence': content})
        self.response_sent.set()
        return {'id': 'preference-response', 'type': 'message', 'role': 'assistant', 'model': 'test-model',
                'content': [{'type': 'text', 'text': json.dumps({'changes': changes}, ensure_ascii=False)}],
                'stop_reason': 'end_turn', 'usage': {'input_tokens': 3, 'output_tokens': 2}}


class ProcessClock:
    def __init__(self, directory):
        self.path = Path(directory) / 'clock'
        self.now = 10000.0
        self.write()

    def write(self):
        replacement = self.path.with_suffix('.next')
        replacement.write_text(str(self.now))
        replacement.replace(self.path)

    async def advance(self, seconds):
        self.now += seconds
        self.write()
        await asyncio.sleep(.08)


async def until(client, path, predicate, *, timeout=5):
    async def polling():
        while True:
            response = await client.get(path)
            response.raise_for_status()
            state = response.json()
            if predicate(state):
                return state
            await asyncio.sleep(.02)
    return await asyncio.wait_for(polling(), timeout)


class PreferenceRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def finish(self, client, text, session_id=None):
        result = await client.post('/api/turns', json={'input': text, 'session_id': session_id})
        self.assertEqual(result.status_code, 202)
        identity = result.json()
        await wait_finished(client, identity['session_id'], identity['turn_id'])
        return identity

    async def test_new_input_invalidates_interrupted_claim_without_spending_its_new_idle_retry(self):
        external = PreferenceServices()
        external.block_extract = True
        with tempfile.TemporaryDirectory() as directory:
            clock = ProcessClock(directory)
            async with external.serving() as url:
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    first = await self.finish(client, '我长期不吃辣')
                    await clock.advance(3600)
                    await asyncio.wait_for(external.extract_entered.wait(), 3)
                    second = await self.finish(client, '我长期喜欢博物馆', first['session_id'])
                    child.kill()
                    await child.wait()
                    external.extract_release.set()
                    external.block_extract = False
                async with process(directory, url, clock_file=clock.path) as (_, client, _):
                    pending = (await client.get('/api/preferences')).json()['processing']
                    self.assertEqual([(p['status'], p['attempts']) for p in pending], [('pending', 0), ('pending', 0)])
                    await clock.advance(3599)
                    self.assertEqual(len(external.extract_calls), 1)
                    await clock.advance(1)
                    state = await until(client, '/api/preferences', lambda s: not s['processing'])
                    self.assertCountEqual([p['content'] for p in state['preferences']], ['不吃辣', '喜欢博物馆'])
                    self.assertEqual([i['turn_id'] for i in external.extract_calls[-1]['new_inputs']], [first['turn_id'], second['turn_id']])

    async def test_management_versions_and_suppression_survive_kill_during_old_extraction(self):
        for operation in ('edit', 'delete'):
            with self.subTest(operation=operation):
                external = PreferenceServices()
                with tempfile.TemporaryDirectory() as directory:
                    clock = ProcessClock(directory)
                    async with external.serving() as url:
                        async with process(directory, url, clock_file=clock.path) as (child, client, _):
                            await self.finish(client, '我长期不吃辣')
                            await clock.advance(3600)
                            initial = await until(client, '/api/preferences', lambda s: bool(s['preferences']))
                            item = initial['preferences'][0]
                            # 尚未合格的旧输入也受管理水位保护。
                            await self.finish(client, '我长期不吃辣，喜欢博物馆')
                            await self.finish(client, '我还是长期不吃辣')
                            external.extract_entered.clear()
                            external.block_extract = True
                            await clock.advance(3600)
                            await asyncio.wait_for(external.extract_entered.wait(), 3)
                            path = '/api/preferences/' + item['id']
                            managed = (await client.patch(path, json={'version': item['version'], 'content': '偏爱清淡'}) if operation == 'edit'
                                       else await client.delete(path + '?version=' + str(item['version'])))
                            self.assertEqual(managed.status_code, 200)
                            expected = managed.json()['preferences']
                            child.kill()
                            await child.wait()
                            external.extract_release.set()
                            external.block_extract = False
                        async with process(directory, url, clock_file=clock.path) as (_, client, _):
                            self.assertEqual((await client.get('/api/preferences')).json()['preferences'], expected)
                            await clock.advance(30)
                            recovered = await until(client, '/api/preferences', lambda s: not s['processing'])
                            self.assertEqual([p for p in recovered['preferences'] if p['category'] == 'diet'], expected)
                            self.assertEqual([p['content'] for p in recovered['preferences'] if p['category'] == 'activity'], ['喜欢博物馆'])
                            await self.finish(client, '新会话推荐餐厅')
                            system = external.model_calls[-1]['system']
                            self.assertNotIn('不吃辣', system)
                            if operation == 'edit':
                                self.assertIn('偏爱清淡', system)
                                self.assertEqual(expected[0]['version'], 2)

    async def test_stopped_input_is_terminal_after_kill_and_earlier_valid_input_recovers(self):
        external = PreferenceServices()
        with tempfile.TemporaryDirectory() as directory:
            clock = ProcessClock(directory)
            async with external.serving() as url:
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    first = await self.finish(client, '我长期不吃辣')
                    external.block = 'model'
                    stopped = (await client.post('/api/turns', json={'input': '我长期喜欢坐火车', 'session_id': first['session_id']})).json()
                    await asyncio.wait_for(external.entered.wait(), 3)
                    response = await client.post('/api/turns/' + stopped['turn_id'] + '/stop')
                    self.assertTrue(response.json()['stopping'])
                    child.kill()
                    await child.wait()
                    external.release.set()
                    external.block = None
                async with process(directory, url, clock_file=clock.path) as (_, client, _):
                    await clock.advance(3599)
                    self.assertEqual(external.extract_calls, [])
                    await clock.advance(1)
                    state = await until(client, '/api/preferences', lambda s: bool(s['preferences']))
                    self.assertEqual([p['content'] for p in state['preferences']], ['不吃辣'])
                    self.assertEqual([(p['turn_id'], p['status']) for p in state['processing']], [(stopped['turn_id'], 'cancelled')])
                    self.assertEqual([i['turn_id'] for i in external.extract_calls[0]['new_inputs']], [first['turn_id']])

    async def test_waiting_and_committed_tasks_survive_kill_without_replaying_travel_or_duplicate_preferences(self):
        external = PreferenceServices()
        with tempfile.TemporaryDirectory() as directory:
            clock = ProcessClock(directory)
            async with external.serving() as url:
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    identity = await self.finish(client, '我长期不吃辣，帮我地图查询')
                    original = (await client.get('/api/sessions/' + identity['session_id'])).json()
                    await clock.advance(3599)
                    self.assertEqual(external.extract_calls, [])
                    child.kill()
                    await child.wait()
                main_calls, map_calls = len(external.model_calls), len(external.map_calls)
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    await asyncio.sleep(.08)
                    self.assertEqual(external.extract_calls, [])
                    await clock.advance(1)
                    saved = await until(client, '/api/preferences', lambda s: len(s['preferences']) == 1)
                    self.assertEqual(saved['processing'], [])
                    self.assertEqual(saved['preferences'][0]['source_turn_id'], identity['turn_id'])
                    child.kill()
                    await child.wait()
                async with process(directory, url, clock_file=clock.path) as (_, client, _):
                    await clock.advance(86400)
                    self.assertEqual((await client.get('/api/preferences')).json(), saved)
                    self.assertEqual((await client.get('/api/sessions/' + identity['session_id'])).json(), original)
                    self.assertEqual(len(external.extract_calls), 1)
                    self.assertEqual(len(external.model_calls), main_calls + 1)
                    self.assertEqual(len(external.map_calls), map_calls)

    async def test_interrupted_request_preserves_attempt_and_backoff_across_repeated_restarts(self):
        external = PreferenceServices()
        external.block_extract = True
        with tempfile.TemporaryDirectory() as directory:
            clock = ProcessClock(directory)
            async with external.serving() as url:
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    await self.finish(client, '我长期不吃辣')
                    await clock.advance(3600)
                    await asyncio.wait_for(external.extract_entered.wait(), 3)
                    state = (await client.get('/api/preferences')).json()
                    self.assertEqual((state['processing'][0]['status'], state['processing'][0]['attempts']), ('processing', 1))
                    child.kill()
                    await child.wait()
                    external.extract_release.set()
                    external.block_extract = False
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    state = (await client.get('/api/preferences')).json()
                    self.assertEqual((state['processing'][0]['status'], state['processing'][0]['attempts']), ('pending', 1))
                    await clock.advance(29)
                    self.assertEqual(len(external.extract_calls), 1)
                    child.kill()
                    await child.wait()
                async with process(directory, url, clock_file=clock.path) as (_, client, _):
                    await clock.advance(1)
                    state = await until(client, '/api/preferences', lambda s: bool(s['preferences']))
                    self.assertEqual(state['processing'], [])
                    self.assertEqual(len(external.extract_calls), 2)
                    self.assertEqual(len([r for r in external.model_calls if not r['system'].startswith('旅行者偏好提取')]), 1)

    async def test_repeated_kills_cannot_reset_the_three_request_limit(self):
        external = PreferenceServices()
        external.block_extract = True
        with tempfile.TemporaryDirectory() as directory:
            clock = ProcessClock(directory)
            async with external.serving() as url:
                for attempt, delay in enumerate((3600, 30, 120), 1):
                    external.extract_entered.clear()
                    external.extract_release = asyncio.Event()
                    async with process(directory, url, clock_file=clock.path) as (child, client, _):
                        if attempt == 1:
                            await self.finish(client, '我长期不吃辣')
                        else:
                            state = (await client.get('/api/preferences')).json()
                            self.assertEqual(state['processing'][0]['attempts'], attempt - 1)
                        await clock.advance(delay)
                        await asyncio.wait_for(external.extract_entered.wait(), 3)
                        state = (await client.get('/api/preferences')).json()
                        self.assertEqual((state['processing'][0]['status'], state['processing'][0]['attempts']), ('processing', attempt))
                        child.kill()
                        await child.wait()
                        external.extract_release.set()
                        await asyncio.sleep(.03)
                async with process(directory, url, clock_file=clock.path) as (_, client, _):
                    state = (await client.get('/api/preferences')).json()
                    self.assertEqual((state['processing'][0]['status'], state['processing'][0]['attempts']), ('failed', 3))
                    self.assertEqual(state['preferences'], [])
                    await clock.advance(86400)
                    self.assertEqual(len(external.extract_calls), 3)

    async def test_response_then_atomic_commit_failure_stops_writes_and_recovers_once(self):
        external = PreferenceServices()
        external.block_extract = True
        with tempfile.TemporaryDirectory() as directory:
            clock = ProcessClock(directory)
            async with external.serving() as url:
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    await self.finish(client, '我长期不吃辣')
                    await clock.advance(3600)
                    await asyncio.wait_for(external.extract_entered.wait(), 3)
                    with closing(sqlite3.connect(Path(directory) / 'travel.sqlite3')) as database, database:
                        database.execute("CREATE TRIGGER preference_commit_failure BEFORE UPDATE ON preference_inputs WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'password=storage-secret'); END")
                    external.extract_release.set()
                    await asyncio.wait_for(external.response_sent.wait(), 3)
                    public = await until(client, '/api/state', lambda s: not s['accepting'])
                    self.assertIn('偏好存储', public['storage_error'])
                    self.assertNotIn('storage-secret', json.dumps(public))
                    state = (await client.get('/api/preferences')).json()
                    self.assertEqual(state['preferences'], [])  # 前面的偏好INSERT也回滚。
                    self.assertEqual((state['processing'][0]['status'], state['processing'][0]['attempts']), ('processing', 1))
                    self.assertEqual((await client.post('/api/turns', json={'input': '普通旅行回答'})).status_code, 503)
                    await clock.advance(3600)
                    self.assertEqual(len(external.extract_calls), 1)
                    child.kill()
                    await child.wait()
                with closing(sqlite3.connect(Path(directory) / 'travel.sqlite3')) as database, database:
                    database.execute('DROP TRIGGER preference_commit_failure')
                external.block_extract = False
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    self.assertEqual((await client.get('/api/preferences')).json()['preferences'], [])
                    await clock.advance(30)
                    saved = await until(client, '/api/preferences', lambda s: bool(s['preferences']))
                    self.assertEqual(saved['processing'], [])
                    child.kill()
                    await child.wait()
                async with process(directory, url, clock_file=clock.path) as (_, client, _):
                    await clock.advance(3600)
                    self.assertEqual((await client.get('/api/preferences')).json(), saved)
                    self.assertEqual(len(external.extract_calls), 2)

    async def test_kill_after_external_response_before_locked_commit_recovers_without_partial_preferences(self):
        external = PreferenceServices()
        external.block_extract = True
        with tempfile.TemporaryDirectory() as directory:
            clock = ProcessClock(directory)
            async with external.serving() as url:
                async with process(directory, url, clock_file=clock.path) as (child, client, _):
                    await self.finish(client, '我长期不吃辣')
                    await clock.advance(3600)
                    await asyncio.wait_for(external.extract_entered.wait(), 3)
                    before = (await client.get('/api/preferences')).json()
                    self.assertEqual(before['processing'][0]['status'], 'processing')
                    database = sqlite3.connect(Path(directory) / 'travel.sqlite3')
                    try:
                        database.execute('BEGIN IMMEDIATE')
                        external.extract_release.set()
                        await asyncio.wait_for(external.response_sent.wait(), 3)
                        # 证据是外部已返回而写事务被锁阻止，不声称观察到子进程完成解析。
                        await asyncio.sleep(.1)
                        child.kill()
                        await child.wait()
                    finally:
                        database.rollback()
                        database.close()
                external.block_extract = False
                async with process(directory, url, clock_file=clock.path) as (_, client, _):
                    public = (await client.get('/api/preferences')).json()
                    self.assertEqual(public['preferences'], [])
                    self.assertEqual(public['processing'][0]['attempts'], 1)
                    await clock.advance(30)
                    saved = await until(client, '/api/preferences', lambda s: bool(s['preferences']))
                    self.assertEqual(len(saved['preferences']), 1)
                    self.assertEqual(saved['processing'], [])
                    self.assertEqual(len(external.extract_calls), 2)
