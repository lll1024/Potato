import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'tests')]
from test_agent import ModelService, response
from test_context_summary import ENV, OLD, summary_response, submit
from test_trace import serving
from test_preferences import PreferenceTests, PreferenceModel, Clock

DATA = Path(sys.argv[2])
SNAPSHOT = DATA / 'migration-snapshot.json'

async def main():
    if sys.argv[1] == 'seed':
        model = ModelService([response([{'type':'text','text':'酒店未预订。'}]), summary_response(), response([{'type':'text','text':'保留酒店。'}])])
        with patch.dict(os.environ, ENV, clear=True):
            async with serving(DATA, model) as client:
                first = await submit(client, OLD)
                saved = await submit(client, '继续', first['session']['session_id'])
                assert any(e['kind'] == 'context.saved' for e in saved['events'])
        clock = Clock()
        support = PreferenceTests()
        async with support.service(DATA, PreferenceModel(), clock) as client:
            await support.finish(client, '我一直不吃辣')
            await clock.advance(3600)
            preferences = (await client.get('/api/preferences')).json()['preferences']
            assert preferences[0]['content'] == '不吃辣'
            snapshot = (await client.get('/api/sessions/' + saved['session']['session_id'])).json()
        SNAPSHOT.write_text(json.dumps({'session':snapshot,'preferences':preferences},ensure_ascii=False))
        print('旧版本已通过公开入口保存会话、有效上下文版本和旅行者偏好。')
    else:
        old = json.loads(SNAPSHOT.read_text())
        model = ModelService([response([{'type':'text','text':'续聊保持要求。'}])])
        with patch.dict(os.environ, ENV, clear=True):
            async with serving(DATA, model) as client:
                sid = old['session']['session']['session_id']
                assert (await client.get('/api/sessions/' + sid)).json() == old['session']
                assert (await client.get('/api/preferences')).json()['preferences'] == old['preferences']
                continued = await submit(client, '继续保留酒店', sid)
                assert continued['turns'][-1]['answer'] == '续聊保持要求。'
                assert 'travel_state_summary' in str(model.requests[0]['messages'])
                assert OLD not in str(model.requests[0]['messages'])
                assert '不吃辣' in model.requests[0]['system']
        print('新版本原样读取迁移前会话与偏好，续聊恢复有效摘要且注入旧偏好；未迁移数据库。')

asyncio.run(main())
