"""公开 HTTP 接口验证已保存完整载荷的搜索与 Unicode 分段。"""
import asyncio
import json
import tempfile
import unittest

from test_agent import ModelService, response
from test_trace import serving, wait_finished


class PayloadSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_finds_unloaded_old_payload_and_distinguishes_fields(self):
        long_text = '杭州🌸' * 2500 + '尾部独有关键词'
        model = ModelService([response([{'type':'text','text':long_text}]), response([{'type':'text','text':'第二轮结束'}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                first = (await client.post('/api/turns',json={'input':'旧轮输入独有词'})).json()
                await asyncio.wait_for(wait_finished(client,first['session_id']),2)
                second = (await client.post('/api/turns',json={'input':'继续','session_id':first['session_id']})).json()
                await asyncio.wait_for(wait_finished(client,first['session_id'],second['turn_id']),2)
                page = (await client.get(f"/api/sessions/{first['session_id']}?limit=1")).json()
                self.assertEqual(page['turns'][0]['ordinal'],2)
                result = await client.get(f"/api/sessions/{first['session_id']}/search",params={'q':'尾部独有关键词'})
                self.assertEqual(result.status_code,200)
                hits = result.json()['matches']
                answer = next(hit for hit in hits if hit['field']=='turn_answer')
                returned = next(hit for hit in hits if hit['field']=='response' and hit['turn_ordinal']==1)
                self.assertEqual(answer['turn_id'],first['turn_id'])
                self.assertEqual(answer['object_id'],first['turn_id'])
                self.assertEqual(returned['object_type'],'request')
                self.assertNotEqual(answer['payload_id'],returned['payload_id'])
                self.assertIn('尾部独有关键词',answer['excerpt'])
                summary = (await client.get(f"/api/sessions/{first['session_id']}/search",params={'q':'尾部独有关键词','scope':'summary'})).json()
                self.assertEqual(summary['matches'],[])
                self.assertEqual(len(model.requests),2)

    async def test_unicode_segments_reassemble_complete_saved_response_without_secrets(self):
        text = '甲🌸乙\n' * 3000 + '尾部完整标记 known-secret https://example.test/?key=known-secret'
        model = ModelService([response([{'type':'text','text':text}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory,model) as client:
                accepted = (await client.post('/api/turns',json={'input':'查看长返回 known-secret'})).json()
                snapshot = await asyncio.wait_for(wait_finished(client,accepted['session_id']),2)
                payload_id = snapshot['requests'][0]['response_payload_id']
                complete = (await client.get('/api/payloads/'+payload_id)).json()['content']
                chunks = []; offset = 0; total = None
                while True:
                    segment = (await client.get('/api/payloads/'+payload_id,params={'offset':offset,'limit':257})).json()
                    self.assertEqual(segment['payload_id'],payload_id)
                    self.assertEqual(segment['offset'],offset)
                    if total is None: total = segment['total']
                    self.assertEqual(segment['total'],total)
                    chunks.append(segment['text'])
                    if segment['next_offset'] is None: break
                    self.assertGreater(segment['next_offset'],offset)
                    offset = segment['next_offset']
                joined = ''.join(chunks)
                self.assertEqual(len(joined),total)
                self.assertEqual(json.loads(joined),complete)
                self.assertTrue(complete['content'][0]['text'].startswith('甲🌸乙\n甲🌸乙\n'))
                self.assertIn('尾部完整标记',joined)
                self.assertNotIn('known-secret',joined)
                self.assertNotIn('https://example.test/',joined)
                search = (await client.get(f"/api/sessions/{accepted['session_id']}/search",params={'q':'尾部完整标记'})).json()
                self.assertIn(payload_id,[hit['payload_id'] for hit in search['matches']])
                self.assertNotIn('known-secret',json.dumps(search,ensure_ascii=False))
                self.assertEqual((await client.get('/api/payloads/'+payload_id,params={'offset':-1})).status_code,422)
                self.assertEqual((await client.get('/api/payloads/'+payload_id,params={'offset':total,'limit':257})).json()['text'],'')

    async def test_search_includes_tool_parameters_service_fill_and_errors_without_new_calls(self):
        from test_tool_trace import serving_tools
        from test_agent import MapService
        from mcp.types import CallToolResult, TextContent
        class Maps(MapService):
            calls = 0
            async def call_tool(self,name,arguments):
                self.calls += 1
                if self.calls == 2: raise ValueError('异常独有匹配词 known-secret')
                return CallToolResult(content=[TextContent(type='text',text='服务独有匹配词')],
                    structured_content={'status':'0','info':'错误独有匹配词 known-secret','Authorization':'unknown-secret'},is_error=False)
        maps = Maps()
        model = ModelService([response([{'type':'tool_use','id':'tool-search','name':'maps_text_search','input':{'keywords':'参数独有匹配词','city':'杭州'}},{'type':'tool_use','id':'tool-error','name':'maps_text_search','input':{'keywords':'西湖','city':'杭州'}}],'tool_use'),response([{'type':'text','text':'本轮结束'}]),response([{'type':'text','text':'新会话结束'}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory,model,maps) as client:
                accepted = (await client.post('/api/turns',json={'input':'查找地图'})).json()
                snapshot = await asyncio.wait_for(wait_finished(client,accepted['session_id']),3)
                call = snapshot['tool_calls'][0]
                for word,field in [('参数独有匹配词','tool_arguments'),('服务独有匹配词','tool_service'),('错误独有匹配词','tool_result'),('异常独有匹配词','tool_error')]:
                    result = (await client.get(f"/api/sessions/{accepted['session_id']}/search",params={'q':word})).json()
                    found = [hit for hit in result['matches'] if hit['field']==field]
                    self.assertTrue(found, (word,field))
                    hit = found[0]
                    expected = snapshot['tool_calls'][1] if field=='tool_error' else call
                    self.assertEqual(hit['object_id'],expected['tool_call_id'])
                    self.assertEqual(hit['request_id'],call['request_id'])
                    self.assertEqual(hit['object_type'],'tool')
                    self.assertNotIn('known-secret',str(result))
                    self.assertNotIn('unknown-secret',str(result))
                self.assertEqual(maps.calls,2)
                self.assertEqual(len(model.requests),2)
                other = (await client.post('/api/turns',json={'input':'新会话'})).json()
                await asyncio.wait_for(wait_finished(client,other['session_id']),2)
                result=(await client.get(f"/api/sessions/{other['session_id']}/search",params={'q':'错误独有匹配词'})).json()
                self.assertEqual(result['matches'],[])

    async def test_search_handles_escaped_text_and_returns_only_safe_effective_query(self):
        model=ModelService([response([{'type':'text','text':'引号"🌸及换行\n杭州 known-secret'}])])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory,model) as client:
                accepted=(await client.post('/api/turns',json={'input':'检索特殊文本'})).json()
                await asyncio.wait_for(wait_finished(client,accepted['session_id']),2)
                for query in ('引号"🌸','换行\n杭州'):
                    result=(await client.get(f"/api/sessions/{accepted['session_id']}/search",params={'q':query})).json()
                    self.assertTrue(result['matches'])
                    for match in result['matches']:
                        self.assertIn(match['highlight'],match['excerpt'])
                safe=(await client.get(f"/api/sessions/{accepted['session_id']}/search",params={'q':'known-secret'})).json()
                self.assertEqual(safe['query'],'[REDACTED]')
                self.assertNotIn('known-secret',json.dumps(safe,ensure_ascii=False))
