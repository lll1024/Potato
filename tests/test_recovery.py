"""真实进程退出和重启，从公开 HTTP/SSE 验证最后保存事实。"""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from mcp.server import MCPServer

from test_trace import wait_finished

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ExternalServices:
    def __init__(self, preference_handler=None):
        self.model_calls = []
        self.map_calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block: str | None = None
        maps = MCPServer('恢复验收地图')

        @maps.tool(name='maps_text_search')
        async def search(keywords: str, city: str) -> str:
            self.map_calls.append((keywords, city))
            if self.block == 'tool':
                self.entered.set()
                await self.release.wait()
            return '西湖位于杭州。'

        mcp_app = maps.streamable_http_app(json_response=True, stateless_http=True)
        @asynccontextmanager
        async def lifespan(app):
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        self.app = FastAPI(lifespan=lifespan)
        self.app.mount('/maps', mcp_app)

        @self.app.post('/v1/messages')
        async def message(request: Request):
            data = await request.json()
            self.model_calls.append(data)
            if preference_handler and data['system'].startswith('旅行者偏好提取'):
                return await preference_handler(data)
            if self.block == 'model':
                self.entered.set()
                await self.release.wait()
            last = data['messages'][-1]['content']
            use_tools = isinstance(last, str) and '地图' in last
            content = ([{'type':'tool_use','id':f'map-{index}','name':'maps_text_search',
                         'input':{'keywords':'西湖','city':'杭州'}} for index in range(2)] if use_tools
                       else [{'type':'text','text':'完整旅行建议。'}])
            return {'id':'response-test','type':'message','role':'assistant','model':'test-model',
                    'content':content,'stop_reason':'tool_use' if use_tools else 'end_turn',
                    'usage':{'input_tokens':3,'output_tokens':2}}

    @asynccontextmanager
    async def serving(self):
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(self.app, host='127.0.0.1', port=port, log_level='critical'))
        task = asyncio.create_task(server.serve())
        try:
            while not server.started and not task.done():
                await asyncio.sleep(0)
            yield f'http://127.0.0.1:{port}'
        finally:
            self.release.set()
            server.should_exit = True
            await task


@asynccontextmanager
async def process(directory, external, *, expected_failure=False, clock_file=None):
    port = free_port()
    env = {**os.environ, 'PYTHONPATH':str(ROOT)}
    arguments = [directory, str(port), external] + ([str(clock_file)] if clock_file else [])
    child = await asyncio.create_subprocess_exec(sys.executable, str(ROOT/'tests/recovery_process.py'),
        *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
    output = b''
    try:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=3) as client:
            async def ready():
                while child.returncode is None:
                    try:
                        if (await client.get('/api/state')).status_code == 200:
                            return True
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.02)
                return False
            available = await asyncio.wait_for(ready(), 10)
            if expected_failure:
                assert not available, '启动应拒绝此数据目录'
                output = await child.stdout.read()
            else:
                assert available, (await child.stdout.read()).decode()
            yield child, client, output.decode()
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
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
                    self.assertEqual([t['status'] for t in before['tool_calls']], ['running','pending'])
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
                    async with client.stream('GET', '/api/events?after=0') as stream:
                        async for line in stream.aiter_lines():
                            if line.startswith('data:') and json.loads(line[5:]).get('kind') == 'tool.waiting':
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
