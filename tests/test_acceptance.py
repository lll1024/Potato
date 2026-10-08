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

    async def test_plain_colon_and_authorization_schemes_are_redacted_before_any_record(self):
        input_text = ('配置片段：\npassword: INPUT-COLON-ONLY\nAuthorization: Bearer INPUT-BEARER-ONLY\n'
                      'Authorization: Digest username="DIGEST-USER-ONLY", nonce="DIGEST-NONCE-ONLY", response="DIGEST-RESPONSE-ONLY"\n'
                      'Cookie: session=COOKIE-SESSION-ONLY; csrf=COOKIE-CSRF-ONLY\n'
                      'Set-Cookie: sid=SET-COOKIE-ONLY; Path=/; HttpOnly\n杭州西湖旅行条件保持完整。')
        answer = '核对配置\nsecret: MODEL-COLON-ONLY\nAuthorization: Basic MODEL-BASIC-ONLY'
        secrets = ['INPUT-COLON-ONLY','INPUT-BEARER-ONLY','MODEL-COLON-ONLY','MODEL-BASIC-ONLY','MAP-BEARER-ONLY','TITLE-COLON-ONLY',
                   'DIGEST-USER-ONLY','DIGEST-NONCE-ONLY','DIGEST-RESPONSE-ONLY','COOKIE-SESSION-ONLY','COOKIE-CSRF-ONLY','SET-COOKIE-ONLY']
        class Maps(MapService):
            async def call_tool(self, name, arguments):
                return CallToolResult(content=[TextContent(type='text',text='西湖\nAuthorization: Bearer MAP-BEARER-ONLY')],is_error=False)
        model = ModelService([
            response([{'type':'tool_use','id':'colon-lake','name':'maps_text_search','input':{'keywords':'西湖','city':'杭州'}}],'tool_use'),
            response([{'type':'text','text':answer}])])
        records = []
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory,model,Maps()) as client:
                accepted = (await client.post('/api/turns',json={'input':input_text})).json()
                saved = await asyncio.wait_for(wait_finished(client,accepted['session_id']),3)
                records.append(saved)
                self.assertNotIn('INPUT-COLON-ONLY',saved['turns'][0]['input'])
                self.assertNotIn('INPUT-BEARER-ONLY',saved['turns'][0]['input'])
                self.assertIn('配置片段',saved['turns'][0]['input'])
                self.assertIn('杭州西湖旅行条件保持完整。',saved['turns'][0]['input'])
                records.append((await client.patch('/api/sessions/'+accepted['session_id'],json={'title':'杭州 password: TITLE-COLON-ONLY'})).json())
                records.append(await asyncio.wait_for(notices(client,'/api/events?after=0'),2))
                for item in saved['turns']+saved['requests']+saved['tool_calls']:
                    for key,value in item.items():
                        if key.endswith('_payload_id') and value:
                            records.append((await client.get('/api/payloads/'+value)).json())
                for secret in secrets:
                    result = (await client.get('/api/sessions/'+accepted['session_id']+'/search',params={'q':secret})).json()
                    self.assertEqual(result['matches'],[])
                records.append((await client.get('/api/sessions/'+accepted['session_id']+'/search',params={'q':input_text})).json())
                self.assertEqual(len(model.requests),2)
                records.extend(model.requests)
                for secret in secrets:
                    self.assertNotIn(secret,json.dumps(records,ensure_ascii=False))
            persisted = b''.join(path.read_bytes() for path in Path(directory).glob('travel.sqlite3*'))
            for secret in secrets:
                self.assertNotIn(secret.encode(),persisted)
