"""最终公开入口贯通：所有可读持久载荷、通知和搜索均已脱敏。"""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from mcp.types import CallToolResult, TextContent

from test_agent import MapService, ModelService, response
from test_reconnect import notices
from test_tool_trace import serving_tools
from test_trace import wait_finished


class FinalAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_are_absent_from_history_trace_search_segments_and_database(self):
        class Maps(MapService):
            calls = 0
            async def call_tool(self, name, arguments):
                self.calls += 1
                return CallToolResult(content=[TextContent(type='text',text='西湖 known-secret')],
                    structured_content={'pois':[{'name':'西湖','token':'service-field-secret'}]},
                    is_error=False)
        maps = Maps()
        model = ModelService([
            response([{'type':'tool_use','id':'lake-safe','name':'maps_text_search',
                       'input':{'keywords':'西湖 known-secret','city':'杭州'}}], 'tool_use'),
            response([{'type':'text','text':'完整回答 known-secret password=answer-field-secret'}])])
        secrets = ['known-secret','input-field-secret','service-field-secret','answer-field-secret','rename-field-secret']
        observed = []
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory, model, maps) as client:
                accepted = (await client.post('/api/turns',json={
                    'input':'查询杭州 known-secret token=input-field-secret', 'submission_id':'safe-acceptance'})).json()
                saved = await asyncio.wait_for(wait_finished(client,accepted['session_id']),3)
                observed.append(saved)
                renamed = await client.patch('/api/sessions/'+accepted['session_id'],
                    json={'title':'杭州 Authorization=rename-field-secret'})
                observed.append(renamed.json())
                observed.append((await client.get('/api/submissions/safe-acceptance')).json())
                observed.append((await client.get('/api/sessions')).json())
                observed.append((await client.get('/api/snapshot')).json())
                observed.append((await client.get('/api/state')).json())
                observed.append(await asyncio.wait_for(notices(client,'/api/events?after=0'),2))
                payload_ids = set()
                for item in saved['turns'] + saved['requests'] + saved['tool_calls']:
                    payload_ids.update(value for key,value in item.items() if key.endswith('_payload_id') and value)
                self.assertTrue(payload_ids)
                for payload_id in payload_ids:
                    complete = (await client.get('/api/payloads/'+payload_id)).json()
                    parts = []; offset = 0
                    while True:
                        chunk = (await client.get('/api/payloads/'+payload_id,
                            params={'offset':offset,'limit':113})).json()
                        parts.append(chunk['text'])
                        if chunk['next_offset'] is None: break
                        offset = chunk['next_offset']
                    joined = ''.join(parts)
                    self.assertEqual(json.loads(joined),complete['content'])
                    observed.extend([complete,joined])
                for scope in ('full','summary'):
                    found = (await client.get('/api/sessions/'+accepted['session_id']+'/search',
                        params={'q':'known-secret','scope':scope})).json()
                    self.assertEqual(found['query'],'[REDACTED]')
                    observed.append(found)
                invalid = await client.post('/api/turns',json={'input':{'token':'input-field-secret'}})
                self.assertEqual(invalid.status_code,422)
                observed.append(invalid.json())
                self.assertEqual(len(model.requests),2)
                self.assertEqual(maps.calls,1)
            # SQLite 文件是交付的本机持久记录；只检查秘密未落盘，不依赖表结构。
            persisted = b''.join(path.read_bytes() for path in Path(directory).glob('travel.sqlite3*'))
            self.assertTrue(persisted)
            for secret in secrets:
                self.assertNotIn(secret,json.dumps(observed,ensure_ascii=False))
                self.assertNotIn(secret.encode(),persisted)
