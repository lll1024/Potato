"""真实进程退出和重启，从公开 HTTP/SSE 验证最后保存事实。"""
import asyncio
import json
from pathlib import Path
import signal
import sqlite3
import tempfile
import unittest

from http_fixtures import wait_finished
from process_fixtures import ExternalServices, process


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_killed_retry_wait_is_closed_without_replaying_the_timer_or_request(self):
        external = ExternalServices()
        external.model_failure = True
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    accepted = (await client.post('/api/turns', json={'input': '等待重试中断'})).json()

                    async def scheduled():
                        while True:
                            saved = (await client.get('/api/sessions/' + accepted['session_id'])).json()
                            if any(event['kind'] == 'request.retry_scheduled' for event in saved['events']):
                                return saved
                            await asyncio.sleep(.01)

                    before = await asyncio.wait_for(scheduled(), 3)
                    self.assertEqual(len(before['requests']), 1)
                    child.kill()
                    await child.wait()
                external.model_failure = False
                async with process(directory, url) as (_, client, _):
                    saved = (await client.get('/api/sessions/' + accepted['session_id'])).json()
                    self.assertEqual(saved['turns'][0]['reason'], 'service_interrupted')
                    self.assertEqual(len(saved['requests']), 1)
                    self.assertEqual(len(external.model_calls), 1)
                    self.assertFalse(any(event['kind'] == 'request.retry_started' for event in saved['events']))
                    next_turn = (await client.post('/api/turns', json={'input': '新的提问', 'session_id': accepted['session_id']})).json()
                    await asyncio.wait_for(wait_finished(client, accepted['session_id'], next_turn['turn_id']), 3)
                    self.assertEqual(len(external.model_calls), 2)
                    self.assertEqual(external.model_calls[-1]['messages'], [{'role': 'user', 'content': '新的提问'}])

    async def test_killed_model_is_observed_once_without_replay_or_context(self):
        external = ExternalServices()
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    complete = (await client.post('/api/turns', json={'input':'此前完整提问'})).json()
                    previous = await wait_finished(client, complete['session_id'])
                    external.block = 'model'
                    accepted = (await client.post('/api/turns', json={'input':'中断提问','session_id':complete['session_id'],'submission_id':'interrupted'})).json()
                    await asyncio.wait_for(external.entered.wait(), 3)
                    before = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    child.kill()
                    await child.wait()
                    external.release.set()
                    external.block = None
                calls = len(external.model_calls)
                async with process(directory, url) as (_, client, _):
                    after = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(after['turns'][-1]['reason'], 'service_interrupted')
                    self.assertEqual(after['turns'][-1]['status'], 'terminated')
                    self.assertTrue(after['turns'][-1]['context_excluded'])
                    self.assertEqual(after['requests'][-1]['status'], 'running')
                    self.assertTrue(after['requests'][-1]['interrupted'])
                    self.assertEqual({k:v for k,v in after['requests'][-1].items() if k!='interrupted'}, before['requests'][-1])
                    self.assertIsNone(after['turns'][-1]['finished_at'])
                    self.assertIsNone(after['turns'][-1]['duration_ms'])
                    self.assertEqual(after['turns'][0], previous['turns'][0])
                    recovery = [e for e in after['events'] if e['kind']=='turn.recovered']
                    self.assertEqual(len(recovery), 1)
                    self.assertGreater(recovery[0]['cursor'], before['cursor'])
                    self.assertEqual(after['stream_id'], before['stream_id'])
                    retry = await client.post('/api/turns', json={'input':'中断提问','session_id':complete['session_id'],'submission_id':'interrupted'})
                    self.assertEqual(retry.json(), accepted)
                    self.assertEqual(len(external.model_calls), calls)
                    new = (await client.post('/api/turns', json={'input':'新提问','session_id':complete['session_id']})).json()
                    final = await wait_finished(client, new['session_id'], new['turn_id'])
                    payload = (await client.get('/api/payloads/'+final['requests'][-1]['input_payload_id'])).json()['content']
                    self.assertEqual(payload['messages'], [{'role':'user','content':'此前完整提问'}, {'role':'assistant','content':[{'type':'text','text':'完整旅行建议。','citations':None}]}, {'role':'user','content':'新提问'}])
                async with process(directory, url) as (_, client, _):
                    stable = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(len([e for e in stable['events'] if e['kind']=='turn.recovered']), 1)
                    self.assertEqual(len(external.model_calls), calls+1)

    async def test_killed_tool_keeps_completed_request_and_pending_batch_facts(self):
        external = ExternalServices()
        external.block = 'tool'
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    accepted = (await client.post('/api/turns', json={'input':'地图查询'})).json()
                    await asyncio.wait_for(external.entered.wait(), 3)
                    before = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual([t['status'] for t in before['tool_calls']], ['running','waiting'])
                    self.assertEqual(before['requests'][0]['status'], 'completed')
                    child.kill()
                    await child.wait()
                    external.release.set()
                    external.block = None
                async with process(directory, url) as (_, client, _):
                    after = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(after['requests'], before['requests'])
                    self.assertEqual([{k:v for k,v in t.items() if k!='interrupted'} for t in after['tool_calls']], before['tool_calls'])
                    self.assertTrue(all(t['interrupted'] for t in after['tool_calls']))
                    self.assertEqual(len(external.model_calls), 1)
                    self.assertEqual(len(external.map_calls), 1)
                    async with client.stream('GET', '/api/events?after='+str(before['cursor'])) as stream:
                        async for line in stream.aiter_lines():
                            if line.startswith('data:'):
                                event = json.loads(line[5:])
                                self.assertEqual(event['kind'], 'turn.recovered')
                                self.assertNotEqual(event['event_id'], before['events'][-1]['event_id'])
                                break

    async def test_sigterm_waits_for_entered_call_and_preserves_real_shutdown(self):
        external = ExternalServices()
        external.block = 'tool'
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    accepted = (await client.post('/api/turns', json={'input':'地图查询'})).json()
                    await asyncio.wait_for(external.entered.wait(), 3)
                    child.send_signal(signal.SIGTERM)
                    # 等 uvicorn graceful 窗口消耗，真正调用仍未返回，不得提前释放。
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(child.wait()), 1.3)
                    external.release.set()
                    external.block = None
                    self.assertEqual(await asyncio.wait_for(child.wait(), 5), -signal.SIGTERM)
                async with process(directory, url) as (_, client, _):
                    saved = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(saved['turns'][0]['reason'], 'service_shutdown')
                    self.assertNotIn('turn.recovered', [e['kind'] for e in saved['events']])
                    self.assertEqual([t['status'] for t in saved['tool_calls']], ['completed','not_executed'])
                    self.assertEqual(len(external.map_calls), 1)
                    self.assertEqual(len(external.model_calls), 1)

    async def test_complete_terminal_survives_kill_and_version_lock_diagnostics(self):
        external = ExternalServices()
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    accepted = (await client.post('/api/turns', json={'input':'已完成提问'})).json()
                    before = await wait_finished(client, accepted['session_id'])
                    async with process(directory, url, expected_failure=True) as (_, _, output):
                        self.assertIn('同一数据目录', output)
                    self.assertEqual((await client.get('/api/state')).status_code, 200)
                    child.kill()
                    await child.wait()
                database_path = Path(directory)/'travel.sqlite3'
                with sqlite3.connect(database_path) as database:
                    database.execute('PRAGMA user_version=999')
                database.close()
                async with process(directory, url, expected_failure=True) as (_, _, output):
                    self.assertIn('数据版本不兼容', output)
                with sqlite3.connect(database_path) as database:
                    self.assertEqual(database.execute('PRAGMA user_version').fetchone()[0], 999)
                    database.execute('PRAGMA user_version=1')
                database.close()
                async with process(directory, url) as (_, client, _):
                    after = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(after, before)
                    self.assertEqual(len(external.model_calls), 1)

    async def test_recovery_write_failure_cannot_accept_or_replay(self):
        external = ExternalServices()
        external.block = 'model'
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    accepted = (await client.post('/api/turns', json={'input':'未完成提问'})).json()
                    await asyncio.wait_for(external.entered.wait(), 3)
                    child.kill()
                    await child.wait()
                    external.release.set()
                    external.block = None
                with sqlite3.connect(Path(directory)/'travel.sqlite3') as database:
                    database.execute("CREATE TRIGGER recovery_failure BEFORE INSERT ON events WHEN NEW.kind='turn.recovered' BEGIN SELECT RAISE(ABORT,'password=recovery-secret'); END")
                database.close()
                async with process(directory, url, expected_failure=True) as (_, _, output):
                    self.assertIn('服务无法启动', output)
                    self.assertNotIn('recovery-secret', output)
                self.assertEqual(len(external.model_calls), 1)
                with sqlite3.connect(Path(directory)/'travel.sqlite3') as database:
                    database.execute('DROP TRIGGER recovery_failure')
                database.close()
                async with process(directory, url) as (_, client, _):
                    after = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(after['turns'][0]['reason'], 'service_interrupted')
                    self.assertEqual(len([e for e in after['events'] if e['kind']=='turn.recovered']), 1)

    async def test_normal_exit_without_saved_turn_terminal_recovers_completed_call(self):
        external = ExternalServices()
        external.block = 'model'
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    accepted = (await client.post('/api/turns', json={'input':'真实返回但整轮未保存'})).json()
                    await asyncio.wait_for(external.entered.wait(), 3)
                    with sqlite3.connect(Path(directory)/'travel.sqlite3') as database:
                        database.execute("CREATE TRIGGER finish_failure BEFORE INSERT ON events WHEN NEW.kind='turn.finished' BEGIN SELECT RAISE(ABORT,'合成保存失败'); END")
                    database.close()
                    external.release.set()
                    external.block = None
                    async def failed_save():
                        while True:
                            state = (await client.get('/api/state')).json()
                            if not state['accepting']:
                                return
                            await asyncio.sleep(0)
                    await asyncio.wait_for(failed_save(), 3)
                    before = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(before['requests'][0]['status'], 'completed')
                    child.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(child.wait(), 5)
                with sqlite3.connect(Path(directory)/'travel.sqlite3') as database:
                    database.execute('DROP TRIGGER finish_failure')
                database.close()
                async with process(directory, url) as (_, client, _):
                    after = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(after['turns'][0]['reason'], 'service_interrupted')
                    self.assertEqual(after['requests'], before['requests'])
                    self.assertTrue(after['turns'][0]['context_excluded'])
                    self.assertEqual(len(external.model_calls), 1)

    async def test_killed_rate_wait_keeps_absent_call_end_and_duration(self):
        external = ExternalServices()
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    accepted = (await client.post('/api/turns', json={'input':'地图查询'})).json()
                    completed = False
                    async with client.stream('GET', '/api/events?after=0') as stream:
                        async for line in stream.aiter_lines():
                            if not line.startswith('data:'):
                                continue
                            kind = json.loads(line[5:]).get('kind')
                            if kind == 'tool.completed':
                                completed = True
                            if kind == 'tool.waiting' and completed:
                                child.kill()
                                await child.wait()
                                break
                self.assertEqual(len(external.map_calls), 1)
                async with process(directory, url) as (_, client, _):
                    after = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                    waiting = after['tool_calls'][1]
                    self.assertEqual(waiting['status'], 'waiting')
                    self.assertFalse(waiting['call_started'])
                    self.assertTrue(waiting['interrupted'])
                    self.assertIsNone(waiting['call_duration_ms'])
                    self.assertIsNone(waiting['total_duration_ms'])
                    self.assertNotIn('finished_at', waiting)
                    self.assertNotIn('call_finished_at', waiting)
                    self.assertEqual(len(external.map_calls), 1)
