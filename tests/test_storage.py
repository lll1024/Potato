"""真实 SQLite 故障经公开 HTTP/SSE 观察，不重放外部查询。"""
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_agent import ModelService, response
from test_trace import serving, wait_finished


def fail_event(directory, kind):
    with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
        database.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events WHEN NEW.kind='" + kind + "' BEGIN SELECT RAISE(ABORT,'password=storage-secret'); END")


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_while_model_entered_keeps_slot_and_waits_for_real_return(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingModel(ModelService):
            async def create(self, **kwargs):
                entered.set()
                await release.wait()
                return await super().create(**kwargs)
        model = WaitingModel([response([{"type":"tool_use","id":"one","name":"maps_text_search","input":{"keywords":"西湖","city":"杭州"}}], "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                accepted = (await client.post("/api/turns", json={"input":"杭州查询"})).json()
                await asyncio.wait_for(entered.wait(), 2)
                try:
                    with closing(sqlite3.connect(Path(directory)/"travel.sqlite3")) as database, database:
                        database.execute("CREATE TRIGGER fail_rename BEFORE UPDATE OF title ON sessions BEGIN SELECT RAISE(ABORT,'password=storage-secret'); END")
                    failed = await client.patch("/api/sessions/"+accepted["session_id"],json={"title":"新标题"})
                    self.assertEqual(failed.status_code,503)
                    state = (await client.get("/api/state")).json()
                    self.assertFalse(state["accepting"])
                    self.assertEqual(state["active_turn_id"],accepted["turn_id"])
                    self.assertIn("存储故障",state["storage_error"])
                    self.assertNotIn("storage-secret",str(state)+failed.text)
                    self.assertEqual((await client.post("/api/turns",json={"input":"别的查询"})).status_code,503)
                    self.assertEqual(model.requests,[])
                finally:
                    release.set()
                saved = await asyncio.wait_for(wait_finished(client,accepted["session_id"]),2)
                self.assertEqual(len(model.requests),1)
                self.assertEqual(saved["turns"][0]["reason"],"storage_failure")
                self.assertEqual(saved["requests"][0]["status"],"completed")
                self.assertTrue(all(not tool["call_started"] for tool in saved["tool_calls"]))
                self.assertFalse((await client.get("/api/state")).json()["accepting"])

    async def test_model_start_failure_never_enters_sdk_and_only_transient_notice_reports_fault(self):
        import json
        model = ModelService([])
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory, model) as client:
                fail_event(directory, 'request.started')
                accepted = (await client.post('/api/turns',json={'input':'查询杭州'})).json()
                while True:
                    state = (await client.get('/api/state')).json()
                    if not state['accepting']: break
                self.assertEqual(model.requests, [])
                self.assertEqual(state['unsaved_fact']['kind'],'request.started')
                saved = (await client.get('/api/sessions/'+accepted['session_id'])).json()
                self.assertEqual(saved['requests'],[])
                self.assertEqual([item['kind'] for item in saved['events']],['turn.accepted'])
                async with client.stream('GET','/api/events?after=0') as stream:
                    event_name, cursor = '', None
                    async for line in stream.aiter_lines():
                        if line.startswith('event:'): event_name=line[7:]
                        if line.startswith('id:'): cursor=line[4:]
                        if line.startswith('data:') and event_name=='service.state':
                            notice=json.loads(line[5:])
                            self.assertTrue(notice['transient'])
                            self.assertNotIn('cursor',notice)
                            self.assertIn('存储故障',notice['state']['storage_error'])
                            self.assertNotIn('storage-secret',line)
                            break
                        if not line: cursor=None
                    self.assertIsNone(cursor)

    async def test_model_result_or_error_rollback_restarts_as_unknown_without_replay(self):
        for fails in (False, True):
            with self.subTest(fails=fails), tempfile.TemporaryDirectory() as directory:
                entered,release=asyncio.Event(),asyncio.Event()
                class WaitingModel(ModelService):
                    calls=0
                    async def create(self,**kwargs):
                        self.calls+=1
                        entered.set(); await release.wait()
                        if fails: raise RuntimeError('password=model-secret')
                        return await super().create(**kwargs)
                model=WaitingModel([response([{'type':'text','text':'已收到 token=result-secret'}])])
                async with serving(directory,model) as client:
                    accepted=(await client.post('/api/turns',json={'input':'未保存轮次 token=input-secret'})).json()
                    await asyncio.wait_for(entered.wait(),2)
                    fail_event(directory,'request.failed' if fails else 'request.completed')
                    release.set()
                    while True:
                        state=(await client.get('/api/state')).json()
                        if state['active_turn_id'] is None: break
                    self.assertIn('未保存',state['unsaved_fact']['message'])
                    saved=(await client.get('/api/sessions/'+accepted['session_id'])).json()
                    request=saved['requests'][0]
                    self.assertEqual(request['status'],'running')
                    for field in ('response_payload_id','error_payload_id','finished_at','duration_ms','usage'):
                        self.assertIsNone(request[field])
                    self.assertEqual([event['kind'] for event in saved['events']],['turn.accepted','request.started'])
                    self.assertEqual(model.calls,1)
                    self.assertEqual((await client.post('/api/turns',json={'input':'其他输入'})).status_code,503)
                    self.assertNotIn('storage-secret',str(state))
                with closing(sqlite3.connect(Path(directory)/'travel.sqlite3')) as database, database: database.execute('DROP TRIGGER fail_event')
                next_model=ModelService([response([{'type':'text','text':'新建议'}])])
                async with serving(directory,next_model) as client:
                    restored=(await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertTrue(restored['turns'][0]['context_excluded'])
                    self.assertTrue(restored['requests'][0]['interrupted'])
                    self.assertEqual(restored['requests'][0]['request_id'],request['request_id'])
                    self.assertEqual(next_model.requests,[])
                    later=(await client.post('/api/turns',json={'input':'新输入','session_id':accepted['session_id']})).json()
                    await asyncio.wait_for(wait_finished(client,accepted['session_id'],later['turn_id']),2)
                    self.assertEqual(next_model.requests[0]['messages'],[{'role':'user','content':'新输入'}])

    async def test_tool_boundaries_rollback_and_stop_following_calls(self):
        from test_agent import MapService
        from test_tool_trace import serving_tools, tool_reply
        for kind in ('tool.pending','tool.running','tool.waiting','tool.completed','tool.failed'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                entered,release=asyncio.Event(),asyncio.Event()
                class ControlledMaps(MapService):
                    calls=0
                    async def call_tool(self,name,arguments):
                        self.calls+=1
                        entered.set(); await release.wait()
                        if kind=='tool.failed': raise RuntimeError('password=map-secret')
                        return await super().call_tool(name,arguments)
                maps=ControlledMaps();model=ModelService([tool_reply(2)])
                async with serving_tools(directory,model,maps) as client:
                    if kind in ('tool.pending','tool.running','tool.waiting'): fail_event(directory,kind)
                    accepted=(await client.post('/api/turns',json={'input':'查询西湖'})).json()
                    if kind in ('tool.completed','tool.failed','tool.waiting'):
                        await asyncio.wait_for(entered.wait(),2)
                        if kind != 'tool.waiting': fail_event(directory,kind)
                    release.set()
                    while True:
                        state=(await client.get('/api/state')).json()
                        if state['active_turn_id'] is None: break
                    self.assertFalse(state['accepting'])
                    self.assertEqual(state['unsaved_fact']['kind'],kind)
                    saved=(await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertEqual(len(model.requests),1)
                    self.assertEqual(maps.calls,0 if kind in ('tool.pending','tool.running') else 1)
                    self.assertEqual(saved['requests'][0]['status'],'completed')
                    self.assertNotIn(kind,[event['kind'] for event in saved['events']])
                    self.assertNotIn('turn.finished',[event['kind'] for event in saved['events']])
                    if kind=='tool.pending': self.assertEqual(saved['tool_calls'],[])
                    else:
                        one=saved['tool_calls'][0]
                        if kind in ('tool.completed','tool.failed'):
                            self.assertEqual(one['status'],'running')
                            self.assertNotIn('service_payload_id',one)
                            self.assertNotIn('result_payload_id',one)
                            self.assertNotIn('error_payload_id',one)
                            self.assertIsNone(one.get('call_duration_ms'))
                        if kind=='tool.waiting':
                            self.assertEqual(one['status'],'completed')
                            self.assertEqual(saved['tool_calls'][1]['status'],'pending')
                    self.assertNotIn('storage-secret',str(state)+str(saved))
                with closing(sqlite3.connect(Path(directory)/'travel.sqlite3')) as database, database: database.execute('DROP TRIGGER fail_event')
                database.close()
                async with serving_tools(directory,ModelService([]),maps) as client:
                    restored=(await client.get('/api/sessions/'+accepted['session_id'])).json()
                    self.assertTrue(restored['turns'][0]['context_excluded'])
                    self.assertEqual(restored['requests'][0]['status'],'completed')
                    self.assertEqual(maps.calls,0 if kind in ('tool.pending','tool.running') else 1)

    async def test_finished_transaction_failure_keeps_request_result_but_excludes_whole_turn_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            model=ModelService([response([{'type':'text','text':'模型已答复'}])])
            async with serving(directory,model) as client:
                fail_event(directory,'turn.finished')
                accepted=(await client.post('/api/turns',json={'input':'查询杭州'})).json()
                while True:
                    state=(await client.get('/api/state')).json()
                    if state['active_turn_id'] is None: break
                saved=(await client.get('/api/sessions/'+accepted['session_id'])).json()
                self.assertEqual(saved['turns'][0]['status'],'running')
                self.assertIsNone(saved['turns'][0]['answer'])
                self.assertEqual(saved['requests'][0]['status'],'completed')
                payload=(await client.get('/api/payloads/'+saved['requests'][0]['response_payload_id'])).json()
                self.assertIn('模型已答复',str(payload))
                self.assertNotIn('turn.finished',[event['kind'] for event in saved['events']])
                self.assertEqual(state['unsaved_fact']['kind'],'turn.finished')
            with closing(sqlite3.connect(Path(directory)/'travel.sqlite3')) as database, database: database.execute('DROP TRIGGER fail_event')
            database.close()
            async with serving(directory,ModelService([])) as client:
                restored=(await client.get('/api/sessions/'+accepted['session_id'])).json()
                self.assertTrue(restored['turns'][0]['context_excluded'])
                self.assertEqual(restored['requests'][0],saved['requests'][0])
                self.assertFalse((await client.get('/api/state')).json()['storage_error'])

    async def test_notification_read_failure_reports_safe_transient_fault_without_fake_cursor(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            async with serving(directory,ModelService([])) as client:
                with closing(sqlite3.connect(Path(directory)/'travel.sqlite3')) as database, database:
                    database.execute('ALTER TABLE events RENAME TO unavailable_events')
                database.close()
                try:
                    async with client.stream('GET','/api/events?after=0') as stream:
                        async for line in stream.aiter_lines():
                            self.assertFalse(line.startswith('id:'))
                            if line.startswith('data:'):
                                notice=json.loads(line[5:])
                                self.assertEqual(notice['kind'],'service.state')
                                self.assertFalse(notice['state']['accepting'])
                                self.assertIn('存储故障',notice['state']['storage_error'])
                                break
                finally:
                    with closing(sqlite3.connect(Path(directory)/'travel.sqlite3')) as database, database:
                        database.execute('ALTER TABLE unavailable_events RENAME TO events')
                    database.close()

    async def test_acceptance_failure_is_atomic_and_submission_read_failure_stops_service(self):
        for kind in ('turn.accepted','submission.read'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                model=ModelService([])
                async with serving(directory,model) as client:
                    if kind=='turn.accepted': fail_event(directory,kind)
                    else:
                        with closing(sqlite3.connect(Path(directory)/'travel.sqlite3')) as database, database:
                            database.execute('ALTER TABLE submissions RENAME TO unavailable_submissions')
                    failed=await client.post('/api/turns',json={'input':'token=input-secret','submission_id':'once'})
                    self.assertEqual(failed.status_code,503)
                    self.assertEqual(failed.json()['detail']['code'],'STORAGE_FAILURE')
                    self.assertNotIn('input-secret',failed.text)
                    self.assertEqual((await client.get('/api/sessions')).json()['sessions'],[])
                    self.assertEqual((await client.get('/api/snapshot')).json()['cursor'],0)
                    state=(await client.get('/api/state')).json()
                    self.assertFalse(state['accepting'])
                    self.assertIsNone(state['active_turn_id'])
                    self.assertEqual(model.requests,[])
                    if kind=='submission.read':
                        with closing(sqlite3.connect(Path(directory)/'travel.sqlite3')) as database, database:
                            database.execute('ALTER TABLE unavailable_submissions RENAME TO submissions')
                    self.assertEqual((await client.get('/api/submissions/once')).status_code,404)
                    self.assertEqual((await client.post('/api/turns',json={'input':'再次发送'})).status_code,503)

    async def test_storage_failure_keeps_all_sources_and_transient_notice_redacted(self):
        import io
        import json
        from contextlib import redirect_stdout,redirect_stderr
        from types import SimpleNamespace
        from test_agent import MapService
        from test_tool_trace import serving_tools
        from urllib.parse import quote,quote_plus
        secret='known-secret'
        original='https://example.test/?key='+secret
        first=response([{'type':'tool_use','id':'one','name':'maps_text_search',
                         'input':{'keywords':'西湖','city':'杭州','Authorization':'args-secret','url':original}}],'tool_use')
        final=response([{'type':'text','text':'建议 token=answer-secret '+quote(secret)+' '+quote_plus(secret)}])
        final.service_meta={'Authorization':'response-secret','url':original}
        class SafeMaps(MapService):
            async def call_tool(self,name,arguments):
                return SimpleNamespace(content=[SimpleNamespace(type='text',text='西湖 token=service-secret '+original)],
                        structured_content={'pois':[{'name':'西湖','Cookie':'cookie-secret'}]},is_error=False)
        model=ModelService([first,final]); output=io.StringIO()
        with tempfile.TemporaryDirectory() as directory,redirect_stdout(output),redirect_stderr(output):
            async with serving_tools(directory,model,SafeMaps()) as client:
                fail_event(directory,'turn.finished')
                accepted=(await client.post('/api/turns',json={'input':'查询 token=input-secret '+original})).json()
                while True:
                    state=(await client.get('/api/state')).json()
                    if state['active_turn_id'] is None: break
                saved=(await client.get('/api/sessions/'+accepted['session_id'])).json()
                references=set()
                for item in saved['requests']+saved['tool_calls']:
                    references.update(value for key,value in item.items() if key.endswith('_payload_id') and value)
                for turn in saved['turns']:
                    references.update(value for key,value in turn.items() if key.endswith('_payload_id') and value)
                payloads=[(await client.get('/api/payloads/'+key)).json() for key in references]
                notices=[]
                async with client.stream('GET','/api/events?after=0') as stream:
                    async for line in stream.aiter_lines():
                        if line.startswith('data:'):
                            event=json.loads(line[5:]);notices.append(event)
                            if event['kind']=='service.state': break
                visible=json.dumps([state,saved,payloads,notices],ensure_ascii=False)+output.getvalue()
                for value in (secret,'input-secret','args-secret','service-secret','cookie-secret','answer-secret','response-secret','storage-secret'):
                    self.assertNotIn(value,visible)
                    self.assertNotIn(quote(value,safe=''),visible)
                    self.assertNotIn(quote_plus(value),visible)
                self.assertNotIn(original,visible)
                self.assertIn('西湖',visible)
                self.assertEqual(len(model.requests),2)
            persisted=(Path(directory)/'travel.sqlite3').read_bytes()
            for value in (secret,'input-secret','args-secret','service-secret','cookie-secret','answer-secret','response-secret'):
                self.assertFalse(value.encode() in persisted, '产品记录含未脱敏凭据')
