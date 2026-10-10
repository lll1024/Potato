"""从公开 HTTP/SSE 验证固定轮次的边界停止。"""
import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from typing import cast

import httpx
from anthropic import AsyncAnthropic, AnthropicError
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService, ModelService, response
from test_trace import wait_finished
from test_tool_trace import serving_tools, tool_reply
from web import Runtime, create_app


@asynccontextmanager
async def local_app(directory, model, maps):
    @asynccontextmanager
    async def resources():
        tools = AmapTools(cast(Client, maps))
        await tools.discover()
        yield Runtime(cast(AsyncAnthropic, model), tools, 'test-model')
    app = create_app(directory, resources=resources)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client


class StopTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_entered_model_waits_keeps_slot_and_skips_all_proposed_tools(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        class CountingMaps(MapService):
            calls = 0
            async def call_tool(self, name, arguments):
                self.calls += 1
                return await super().call_tool(name, arguments)
        model, maps = WaitingModel([tool_reply(3)]), CountingMaps()
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory, model, maps) as client:
                accepted = (await client.post('/api/turns', json={'input':'查询杭州西湖'})).json()
                await entered.wait()
                try:
                    stopped = await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                    self.assertEqual(stopped.status_code, 200)
                    self.assertTrue(stopped.json()['stopping'])
                    again = await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                    self.assertEqual(again.json(), stopped.json())
                    state = (await client.get('/api/state')).json()
                    self.assertEqual(state['active_turn_id'], accepted['turn_id'])
                    self.assertTrue(state['stopping'])
                    self.assertEqual((await client.post('/api/turns', json={'input':'另一轮'})).status_code,409)
                    self.assertEqual((await client.delete('/api/sessions/'+accepted['session_id'])).status_code,409)
                finally:
                    release.set()
                saved = await wait_finished(client, accepted['session_id'])
                turn = saved['turns'][0]
                self.assertEqual((turn['status'],turn['reason'],turn['answer_source']),('terminated','user_stop','application'))
                self.assertEqual(maps.calls,0)
                self.assertEqual(len(model.requests),1)
                self.assertEqual(saved['requests'][0]['status'],'completed')
                self.assertEqual(len(saved['tool_calls']),3)
                for tool in saved['tool_calls']:
                    self.assertEqual((tool['status'],tool['reason'],tool['call_started']),('not_executed','user_stop',False))
                    self.assertIsNone(tool['call_duration_ms'])
                    self.assertNotIn('service_payload_id',tool)
                    fill = (await client.get('/api/payloads/'+tool['result_payload_id'])).json()['content']
                    self.assertEqual(fill['tool_use_id'],tool['tool_use_id'])
                late = await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                self.assertFalse(late.json()['stopping'])
                self.assertEqual((await client.get('/api/sessions/'+accepted['session_id'])).json(),saved)
                next_turn = (await client.post('/api/turns',json={'input':'新的查询'})).json()
                next_saved = await wait_finished(client,next_turn['session_id'])
                self.assertNotEqual(next_saved['turns'][0]['reason'],'user_stop')

    async def test_stop_before_background_call_prevents_sdk_and_unknown_target_is_safe(self):
        model = ModelService([response([{'type':'text','text':'不应调用'}])])
        with tempfile.TemporaryDirectory() as directory:
            async with local_app(directory, model, MapService()) as client:
                accepted = (await client.post('/api/turns',json={'input':'还未调用'})).json()
                unknown = await client.post('/api/turns/absent/stop')
                self.assertEqual(unknown.status_code,404)
                self.assertEqual(unknown.json()['detail']['code'],'TURN_NOT_FOUND')
                stopped = await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                self.assertTrue(stopped.json()['stopping'])
                await asyncio.sleep(0)
                saved = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                self.assertEqual(saved['turns'][0]['reason'],'user_stop')
                self.assertEqual(saved['requests'],[])
                self.assertEqual(model.requests,[])

    async def test_stop_entered_map_preserves_real_result_and_skips_remaining_batch(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingMaps(MapService):
            calls = 0
            async def call_tool(self,name,arguments):
                self.calls += 1
                entered.set()
                await release.wait()
                return await super().call_tool(name,arguments)
        model, maps = ModelService([tool_reply(3)]), WaitingMaps()
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory,model,maps) as client:
                accepted = (await client.post('/api/turns',json={'input':'查询三个地点'})).json()
                await entered.wait()
                try:
                    stopped = await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                    self.assertTrue(stopped.json()['stopping'])
                    async with client.stream('GET','/api/events') as stream:
                        async for line in stream.aiter_lines():
                            if line.startswith('data:'):
                                event = json.loads(line[5:])
                                if event['kind']=='service.state':
                                    self.assertTrue(event['state']['stopping'])
                                    self.assertEqual(event['state']['active_turn_id'],accepted['turn_id'])
                                    break
                    self.assertEqual((await client.get('/api/sessions/'+accepted['session_id'])).json()['tool_calls'][0]['status'],'running')
                finally: release.set()
                saved = await wait_finished(client,accepted['session_id'])
                first,*others = saved['tool_calls']
                self.assertEqual(first['status'],'completed')
                self.assertTrue(first['call_started'])
                self.assertGreaterEqual(first['call_duration_ms'],0)
                self.assertIn('service_payload_id',first)
                self.assertEqual([tool['status'] for tool in others],['not_executed','not_executed'])
                self.assertEqual(saved['turns'][0]['reason'],'user_stop')
                self.assertEqual(saved['turns'][0]['tool_error_count'],0)
                self.assertEqual(maps.calls,1)
                self.assertEqual(len(model.requests),1)
                messages=saved['turns'][0]['messages']
                results=messages[-2]['content']
                self.assertEqual([result['tool_use_id'] for result in results],['lake-0','lake-1','lake-2'])
                self.assertEqual([result['is_error'] for result in results],[False,True,True])
                self.assertNotIn('查到一个地点',saved['turns'][0]['answer'])
                raw = (await client.get('/api/payloads/'+first['result_payload_id'])).json()
                self.assertIn('查到一个地点',str(raw))

    async def test_rate_limit_wait_can_stop_without_second_sdk_call(self):
        class CountingMaps(MapService):
            calls=0
            async def call_tool(self,name,arguments):
                self.calls+=1
                return await super().call_tool(name,arguments)
        maps=CountingMaps()
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory,ModelService([tool_reply(3)]),maps) as client:
                accepted=(await client.post('/api/turns',json={'input':'限速中停止'})).json()
                async with client.stream('GET','/api/events') as stream:
                    async for line in stream.aiter_lines():
                        if line.startswith('data:') and json.loads(line[5:])['kind']=='tool.waiting':
                            break
                    self.assertEqual(maps.calls,1)
                    await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                saved=await wait_finished(client,accepted['session_id'])
                _,waiting,pending=saved['tool_calls']
                self.assertEqual([waiting['status'],pending['status']],['not_executed','not_executed'])
                self.assertEqual(waiting['reason'],'user_stop')
                self.assertFalse(waiting['call_started'])
                self.assertIsNone(waiting['call_duration_ms'])
                self.assertGreaterEqual(waiting['wait_duration_ms'],0)
                self.assertEqual(maps.calls,1)

    async def test_natural_model_final_output_limit_and_real_failure_win_stop_race(self):
        for ending in ('answer','output_limit','failure'):
            with self.subTest(ending=ending):
                entered,release=asyncio.Event(),asyncio.Event()
                class WaitingModel(ModelService):
                    async def create(self,**kwargs):
                        entered.set();await release.wait()
                        if ending=='failure':
                            self.requests.append(kwargs)
                            raise AnthropicError('模型确实失败')
                        return await super().create(**kwargs)
                model=WaitingModel([response([{'type':'text','text':'真实最终回答'}], 'max_tokens' if ending=='output_limit' else 'end_turn')])
                with tempfile.TemporaryDirectory() as directory:
                    async with serving_tools(directory,model,MapService()) as client:
                        accepted=(await client.post('/api/turns',json={'input':'终局竞争'})).json()
                        await entered.wait()
                        try: await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                        finally: release.set()
                        saved=await wait_finished(client,accepted['session_id'])
                        turn=saved['turns'][0]
                        expected={'answer':('completed',None,'model'),'output_limit':('terminated','output_limit','model'),'failure':('failed','model_error','application')}[ending]
                        self.assertEqual((turn['status'],turn['reason'],turn['answer_source']),expected)
                        self.assertEqual(saved['requests'][0]['status'],'failed' if ending=='failure' else 'completed')
                        before=json.dumps(saved,sort_keys=True)
                        await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                        self.assertEqual(json.dumps((await client.get('/api/sessions/'+accepted['session_id'])).json(),sort_keys=True),before)
                        self.assertEqual(len(model.requests),1)

    async def test_map_pause_survives_stop_and_real_failure_remains_recorded(self):
        from mcp.types import CallToolResult,TextContent
        entered,release=asyncio.Event(),asyncio.Event()
        class FailedMaps(MapService):
            async def call_tool(self,name,arguments):
                entered.set();await release.wait()
                return CallToolResult(content=[TextContent(type='text',text='鉴权失败')],
                    structured_content={'status':'0','infocode':'10001'},is_error=False)
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory,ModelService([tool_reply(1)]),FailedMaps()) as client:
                accepted=(await client.post('/api/turns',json={'input':'停止不解除地图暂停'})).json()
                await entered.wait()
                try: await client.post('/api/turns/'+accepted['turn_id']+'/stop')
                finally: release.set()
                saved=await wait_finished(client,accepted['session_id'])
                self.assertEqual(saved['tool_calls'][0]['status'],'failed')
                self.assertEqual(saved['tool_calls'][0]['failure_category'],'auth')
                self.assertEqual(saved['turns'][0]['reason'],'user_stop')
                self.assertIn('已停止本轮查询',saved['turns'][0]['answer'])
                self.assertTrue((await client.get('/api/state')).json()['map_paused'])
                self.assertTrue((await client.get('/api/state')).json()['accepting'])

    async def test_late_stop_old_turn_does_not_stop_new_active_turn(self):
        entered,release=asyncio.Event(),asyncio.Event()
        class NextWaitingModel(ModelService):
            async def create(self,**kwargs):
                if self.requests:
                    entered.set();await release.wait()
                return await super().create(**kwargs)
        model=NextWaitingModel([response([{'type':'text','text':'第一轮完成'}]),response([{'type':'text','text':'第二轮真实完成'}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory,model,MapService()) as client:
                first=(await client.post('/api/turns',json={'input':'第一轮'})).json()
                saved=await wait_finished(client,first['session_id'])
                second=(await client.post('/api/turns',json={'input':'第二轮','session_id':first['session_id']})).json()
                await entered.wait()
                try:
                    late=(await client.post('/api/turns/'+first['turn_id']+'/stop')).json()
                    self.assertFalse(late['stopping'])
                    state=(await client.get('/api/state')).json()
                    self.assertEqual(state['active_turn_id'],second['turn_id'])
                    self.assertFalse(state['stopping'])
                finally: release.set()
                finished=await wait_finished(client,second['session_id'],second['turn_id'])
                self.assertEqual(finished['turns'][0],saved['turns'][0])
                self.assertEqual(finished['turns'][1]['status'],'completed')
