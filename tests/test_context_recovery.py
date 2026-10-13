"""通过公开 HTTP/SSE、真实进程和 SQLite 验证恢复；测试窗口刻意小于本机 1M。"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from test_recovery import ExternalServices, process
from test_trace import wait_finished


class ContextRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_material_is_short_index_and_readable_by_payload_identity(self):
        source = '中断独有资料：早餐价格120元；来源日期2026-10-10。' + '资料' * 3000
        # 替身只位于外部模型/地图服务边界，生产 loop 与 SQLite 保持真实。
        calls = 0
        async def message(data):
            nonlocal calls
            calls += 1
            if calls == 1:
                content = [{'type': 'tool_use', 'id': 'source', 'name': 'maps_text_search', 'input': {'keywords': '西湖', 'city': '杭州'}}]
            elif calls == 2:
                external.entered.set()
                await external.release.wait()
                content = [{'type': 'text', 'text': '迟到回答'}]
            elif calls == 3:
                system = data['system']
                material = json.loads(system.split('<saved_source_materials>')[1].split('</saved_source_materials>')[0])[0]
                content = [{'type': 'tool_use', 'id': 'read', 'name': 'session_history_read', 'input': {'record_id': material['record_id'], 'limit': 4000}}]
            else:
                content = [{'type': 'text', 'text': '历史资料仍需核对。'}]
            return {'id': 'result', 'type': 'message', 'role': 'assistant', 'model': 'test-model', 'content': content,
                    'stop_reason': 'tool_use' if content[0]['type'] == 'tool_use' else 'end_turn', 'usage': {'input_tokens': 3, 'output_tokens': 2}}
        external = ExternalServices(model_handler=message, map_result=source)
        with patch.dict(os.environ, {'CONTEXT_COMPACTION_ENABLED': 'true', 'MODEL_CONTEXT_WINDOW': '60000'}, clear=True), tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    accepted = (await client.post('/api/turns', json={'input': '2026-10-18查询早餐'})).json()
                    await asyncio.wait_for(external.entered.wait(), 4)
                    child.kill()
                    await child.wait()
                    external.release.set()
                async with process(directory, url) as (_, client, _):
                    self.assertEqual(calls, 2)
                    continued = (await client.post('/api/turns', json={'input': '2026-10-19读取早餐资料', 'session_id': accepted['session_id']})).json()
                    saved = await asyncio.wait_for(wait_finished(client, accepted['session_id'], continued['turn_id']), 4)
                    self.assertEqual(saved['turns'][-1]['answer'], '历史资料仍需核对。')
                    self.assertEqual(calls, 4)
                    data = external.model_calls[2]
                    material = json.loads(data['system'].split('<saved_source_materials>')[1].split('</saved_source_materials>')[0])[0]
                    self.assertNotIn(source, data['system'])
                    self.assertLessEqual(len(material['excerpt']), 200)
                    self.assertEqual(material['verification_status'], 'historical_requires_recheck')
                    self.assertEqual(material['travel_dates']['start_date'], '2026-10-18')
                    self.assertTrue(material['recheck_required'])
                    self.assertIsNotNone(material['retrieved_at'])
                    checks = [event['data'] for event in saved['events'] if event['turn_id'] == continued['turn_id'] and event['kind'] == 'context.checked']
                    self.assertTrue(checks)
                    self.assertTrue(all(check['estimated_tokens'] <= 46000 for check in checks))
                    self.assertNotIn('result', material)
                    self.assertNotIn('protocol_reference', material)
                    self.assertTrue(material['record_id'].startswith('payload:'))
                    self.assertEqual(data['messages'], [{'role': 'user', 'content': '2026-10-19读取早餐资料'}])
                    page = json.loads(external.model_calls[3]['messages'][-1]['content'][0]['content'])
                    self.assertIn('早餐价格120元', page['text'])
                    self.assertEqual(len(page['text']), 4000)
                    self.assertEqual(page['history_status'], 'completed')
                    self.assertEqual(page['turn_reason'], 'service_interrupted')
                    self.assertNotIn('protocol_reference', page)
                    self.assertEqual(page['next_offset'], 4000)

    async def test_history_storage_read_failure_stops_without_saved_completion(self):
        import sqlite3
        from pathlib import Path
        from test_agent import ModelService, response
        from test_trace import serving
        from test_storage import wait_state
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                if len(self.requests) == 1:
                    entered.set()
                    await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{'type': 'text', 'text': '上轮完成'}]),
            response([{'type': 'tool_use', 'id': 'read', 'name': 'session_history_search', 'input': {'query': '早餐'}}], 'tool_use')])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = (await client.post('/api/turns', json={'input': '早餐价格待核实'})).json()
                await wait_finished(client, first['session_id'], first['turn_id'])
                accepted = (await client.post('/api/turns', json={'input': '回读早餐', 'session_id': first['session_id']})).json()
                await asyncio.wait_for(entered.wait(), 2)
                with sqlite3.connect(Path(directory) / 'travel.sqlite3') as database:
                    database.execute("UPDATE payloads SET content='invalid JSON' WHERE turn_id=? AND kind='turn_input'", (first['turn_id'],))
                release.set()
                state = await wait_state(client, lambda item: item['active_turn_id'] is None)
                self.assertFalse(state['accepting'])
                self.assertEqual(state['unsaved_fact']['kind'], 'tool.history_read')
                saved = (await client.get('/api/sessions/' + first['session_id'])).json()
                self.assertFalse(any(event['turn_id'] == accepted['turn_id'] and event['kind'] in ('turn.finished', 'tool.completed', 'tool.failed') for event in saved['events']))
                self.assertEqual(len(model.requests), 2)

    async def test_killed_summary_or_adopted_candidate_restores_previous_saved_version(self):
        from test_context_summary import ENV, OLD
        for boundary in ('summary_inflight', 'adopted'):
            with self.subTest(boundary=boundary), patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
                summary_calls = 0
                async def model(data):
                    nonlocal summary_calls
                    if '整理旅行状态' in data['system']:
                        summary_calls += 1
                        if summary_calls == 2 and boundary == 'summary_inflight':
                            external.entered.set()
                            await external.release.wait()
                        source = json.loads(data['messages'][0]['content'])
                        refs = [record['record_id'] for record in source['records'] if record.get('reference_readable')]
                        value = {'travel_conditions': {'budget': '3000元', 'hotel': '西湖酒店',
                                 'detail': ('上一有效版本' + '旧' * 1400) if summary_calls == 1 else '仅本轮候选'},
                                 'confirmed_itinerary': [], 'corrections': [], 'unresolved': [],
                                 'sources': [{'record_id': refs[0], 'verification': '用户要求'}]}
                        content = [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]
                    else:
                        if summary_calls == 2 and boundary == 'adopted':
                            external.entered.set()
                            await external.release.wait()
                        content = [{'type': 'text', 'text': '旅行回答'}]
                    return {'id': 'result', 'type': 'message', 'role': 'assistant', 'model': 'test-model',
                            'content': content, 'stop_reason': 'end_turn', 'usage': {'input_tokens': 3, 'output_tokens': 2}}
                external = ExternalServices(model_handler=model)
                async with external.serving() as url:
                    async with process(directory, url) as (child, client, _):
                        first = (await client.post('/api/turns', json={'input': OLD})).json()
                        await wait_finished(client, first['session_id'], first['turn_id'])
                        good = (await client.post('/api/turns', json={'input': '继续', 'session_id': first['session_id']})).json()
                        previous = await wait_finished(client, first['session_id'], good['turn_id'])
                        self.assertTrue(any(event['kind'] == 'context.saved' for event in previous['events']))
                        interrupted = (await client.post('/api/turns', json={'input': '仅中断轮次要求' + '新' * 3800, 'session_id': first['session_id']})).json()
                        await asyncio.wait_for(external.entered.wait(), 4)
                        child.kill()
                        await child.wait()
                        external.release.set()
                    calls = len(external.model_calls)
                    async with process(directory, url) as (_, client, _):
                        restored = (await client.get('/api/sessions/' + first['session_id'])).json()
                        self.assertEqual(len(external.model_calls), calls)
                        current = [event for event in restored['events'] if event['turn_id'] == interrupted['turn_id']]
                        discarded = next(event for event in current if event['kind'] == 'context.discarded')
                        self.assertEqual(discarded['data']['used_in_turn'], boundary == 'adopted')
                        self.assertEqual(discarded['data']['result_unknown'], boundary == 'summary_inflight')
                        self.assertFalse(any(event['kind'] == 'context.saved' for event in current))
                        self.assertEqual(restored['requests'][-1]['status'], 'running')
                        self.assertTrue(restored['requests'][-1]['interrupted'])
                        continued = (await client.post('/api/turns', json={'input': '恢复继续', 'session_id': first['session_id']})).json()
                        await wait_finished(client, first['session_id'], continued['turn_id'])
                        request = external.model_calls[-1]
                        self.assertIn('上一有效版本', str(request['messages']))
                        self.assertNotIn('仅本轮候选', str(request['messages']))
                        self.assertNotIn('仅中断轮次要求', str(request['messages']))
                        self.assertEqual(summary_calls, 2)

    async def test_storage_boundaries_then_process_exit_do_not_promote_candidate(self):
        import sqlite3
        from pathlib import Path
        from test_context_summary import ENV, OLD
        from test_storage import fail_event, wait_state
        for boundary in ('context.candidate', 'context.adopted', 'context.saved', 'turn.finished'):
            with self.subTest(boundary=boundary), patch.dict(os.environ, ENV, clear=True), tempfile.TemporaryDirectory() as directory:
                summaries = 0
                async def model(data):
                    nonlocal summaries
                    if '整理旅行状态' in data['system']:
                        summaries += 1
                        source = json.loads(data['messages'][0]['content'])
                        ref = next(item['record_id'] for item in source['records'] if item.get('reference_readable'))
                        value = {'travel_conditions': {'hotel': '西湖酒店', 'budget': '3000元',
                                 'detail': '已保存旧状态' + '旧' * 1400 if summaries == 1 else '未保存候选'},
                                 'confirmed_itinerary': [], 'corrections': [], 'unresolved': [],
                                 'sources': [{'record_id': ref, 'verification': '用户要求'}]}
                        content = [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]
                    else:
                        content = [{'type': 'text', 'text': '完整回答'}]
                    return {'id': 'result', 'type': 'message', 'role': 'assistant', 'model': 'test-model',
                            'content': content, 'stop_reason': 'end_turn', 'usage': {'input_tokens': 3, 'output_tokens': 2}}
                external = ExternalServices(model_handler=model)
                async with external.serving() as url:
                    async with process(directory, url) as (child, client, _):
                        first = (await client.post('/api/turns', json={'input': OLD})).json()
                        await wait_finished(client, first['session_id'], first['turn_id'])
                        good = (await client.post('/api/turns', json={'input': '继续', 'session_id': first['session_id']})).json()
                        await wait_finished(client, first['session_id'], good['turn_id'])
                        fail_event(directory, boundary)
                        failed = (await client.post('/api/turns', json={'input': '故障轮次要求' + '新' * 3800, 'session_id': first['session_id']})).json()
                        state = await wait_state(client, lambda item: not item['accepting'] and item['active_turn_id'] is None)
                        self.assertIsNotNone(state['storage_error'])
                        saved = (await client.get('/api/sessions/' + first['session_id'])).json()
                        events = [event['kind'] for event in saved['events'] if event['turn_id'] == failed['turn_id']]
                        self.assertIn('context.summary_result', events)
                        self.assertNotIn('context.saved', events)
                        self.assertNotIn('turn.finished', events)
                        self.assertEqual('context.candidate' in events, boundary != 'context.candidate')
                        self.assertEqual('context.adopted' in events, boundary in ('context.saved', 'turn.finished'))
                        self.assertIsNone(saved['turns'][-1]['answer'])
                        self.assertEqual(len(external.model_calls), 4 if boundary in ('context.candidate', 'context.adopted') else 5)
                        child.kill()
                        await child.wait()
                    with sqlite3.connect(Path(directory) / 'travel.sqlite3') as database:
                        database.execute('DROP TRIGGER fail_event')
                    calls = len(external.model_calls)
                    async with process(directory, url) as (_, client, _):
                        restored = (await client.get('/api/sessions/' + first['session_id'])).json()
                        self.assertEqual(len(external.model_calls), calls)
                        self.assertTrue(restored['turns'][-1]['context_excluded'])
                        continued = (await client.post('/api/turns', json={'input': '安全续聊', 'session_id': first['session_id']})).json()
                        await wait_finished(client, first['session_id'], continued['turn_id'])
                        self.assertIn('已保存旧状态', str(external.model_calls[-1]['messages']))
                        self.assertNotIn('未保存候选', str(external.model_calls[-1]['messages']))
                        self.assertNotIn('故障轮次要求', str(external.model_calls[-1]['messages']))

    async def test_same_text_from_interrupted_turn_does_not_bind_completed_protocol(self):
        async def model(data):
            number = len(external.model_calls)
            if number in (1, 3):
                content = [{'type': 'tool_use', 'id': f'map-{number}', 'name': 'maps_text_search', 'input': {'keywords': '西湖', 'city': '杭州'}}]
            elif number == 2:
                external.entered.set()
                await external.release.wait()
                content = [{'type': 'text', 'text': '迟到回答'}]
            elif number == 5:
                content = [{'type': 'tool_use', 'id': 'search', 'name': 'session_history_search', 'input': {'query': '西湖位于杭州'}}]
            else:
                content = [{'type': 'text', 'text': '仅作待核实历史资料'}]
            return {'id': 'result', 'type': 'message', 'role': 'assistant', 'model': 'test-model',
                    'content': content, 'stop_reason': 'tool_use' if content[0]['type'] == 'tool_use' else 'end_turn',
                    'usage': {'input_tokens': 3, 'output_tokens': 2}}
        external = ExternalServices(model_handler=model)
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                async with process(directory, url) as (child, client, _):
                    interrupted = (await client.post('/api/turns', json={'input': '同文查询'})).json()
                    await asyncio.wait_for(external.entered.wait(), 4)
                    child.kill()
                    await child.wait()
                    external.release.set()
                async with process(directory, url) as (_, client, _):
                    complete = (await client.post('/api/turns', json={'input': '同文查询', 'session_id': interrupted['session_id']})).json()
                    await wait_finished(client, complete['session_id'], complete['turn_id'])
                    search = (await client.post('/api/turns', json={'input': '搜索西湖历史', 'session_id': complete['session_id']})).json()
                    await wait_finished(client, complete['session_id'], search['turn_id'])
                    matches = json.loads(external.model_calls[-1]['messages'][-1]['content'][0]['content'])['matches']
                    self.assertEqual(len(matches), 2)
                    old, valid = matches
                    self.assertEqual(old['turn_reason'], 'service_interrupted')
                    self.assertNotIn('protocol_reference', old)
                    self.assertTrue(old['record_id'].startswith('payload:'))
                    self.assertEqual(valid['turn_status'], 'completed')
                    self.assertEqual(valid['protocol_reference'], 'message:3:block:0')

    async def test_cli_cancelled_summary_exits_and_new_run_has_no_previous_context(self):
        import io
        from contextlib import asynccontextmanager, redirect_stdout
        from travel_app.agent import run_cli
        from test_agent import MapService, ModelService, response
        from test_context_summary import ENV, OLD
        entered = asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                if '整理旅行状态' in kwargs['system']:
                    self.requests.append(kwargs)
                    entered.set()
                    await asyncio.Event().wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{'type': 'text', 'text': '第一轮完整建议'}])])
        @asynccontextmanager
        async def external_model(*args, **kwargs):
            yield model
        @asynccontextmanager
        async def external_maps(*args, **kwargs):
            yield MapService()
        output = io.StringIO()
        with patch.dict(os.environ, {**ENV, 'MODEL_ID': 'test-model'}, clear=True), patch('travel_app.agent.Client', side_effect=external_maps), patch('travel_app.travel_tools.Client', side_effect=RuntimeError('资料服务不可用')), patch('travel_app.agent.AsyncAnthropic', side_effect=external_model), patch('travel_app.agent.streamable_http_client', return_value=None), patch('builtins.input', side_effect=[OLD, '继续']), redirect_stdout(output):
            task = asyncio.create_task(run_cli('test-key'))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(model.requests), 2)
        self.assertIn('正在整理', output.getvalue())
        self.assertNotIn('摘要已用于本轮', output.getvalue())
        model = ModelService([response([{'type': 'text', 'text': '新会话建议'}])])
        with patch.dict(os.environ, {**ENV, 'MODEL_ID': 'test-model'}, clear=True), patch('travel_app.agent.Client', side_effect=external_maps), patch('travel_app.travel_tools.Client', side_effect=RuntimeError('资料服务不可用')), patch('travel_app.agent.AsyncAnthropic', side_effect=external_model), patch('travel_app.agent.streamable_http_client', return_value=None), patch('builtins.input', side_effect=['新会话', 'exit']), redirect_stdout(io.StringIO()):
            self.assertEqual(await run_cli('test-key'), 0)
        self.assertEqual(model.requests[0]['messages'], [{'role': 'user', 'content': '新会话'}])
