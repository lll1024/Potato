"""真实兼容模型中文验收；显式运行，使用公开 HTTP / SQLite / 生命周期。

用 --output 保存脱敏结果，--case 定向复验。地图为替身，不核实地点事实。
配置仅从指定文件在内存读取，不输出 endpoint、凭据或异常原文。
"""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from contextlib import asynccontextmanager
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from mcp import Client
from amap_mcp import AmapTools
from test_agent import MapService
from test_preferences import Clock
from web import Runtime, create_app


class Evaluation:
    def __init__(self, model):
        self.model = model
        self.calls = []
        self.steps = []
        self.clock = Clock()

    @asynccontextmanager
    async def service(self, directory):
        @asynccontextmanager
        async def resources():
            async with AsyncAnthropic(api_key=os.environ['ANTHROPIC_API_KEY'], base_url=os.environ['ANTHROPIC_BASE_URL'], max_retries=0, timeout=60) as real:
                evaluation = self
                class ObservedModel:
                    messages = None
                    def __init__(self):
                        self.messages = self
                    async def create(self, **kwargs):
                        extraction = kwargs['system'].startswith('旅行者偏好提取')
                        call: dict[str, Any] = {'purpose': 'preference_extraction' if extraction else 'travel_answer'}
                        if extraction:
                            call['input'] = json.loads(kwargs['messages'][0]['content'])
                        else:
                            call['background'] = kwargs['system'].split('旅行者偏好背景数据', 1)[-1] if '旅行者偏好背景数据' in kwargs['system'] else ''
                        evaluation.calls.append(call)
                        try:
                            result = await real.messages.create(**kwargs)
                            call['stop_reason'] = result.stop_reason
                            call['text'] = ''.join(b.text for b in result.content if b.type == 'text')
                            return result
                        except Exception as error:
                            call['error_type'] = type(error).__name__
                            call['status_code'] = getattr(error, 'status_code', None)
                            raise
                tools = AmapTools(cast(Client, MapService()), secrets=(os.environ['ANTHROPIC_API_KEY'],))
                await tools.discover()
                yield Runtime(cast(AsyncAnthropic, ObservedModel()), tools, self.model)
        app = create_app(directory, resources=resources, preference_clock=self.clock, preference_wait=self.clock.wait)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                yield client

    async def turn(self, client, text, session=None):
        accepted = await client.post('/api/turns', json={'input': text, 'session_id': session})
        accepted.raise_for_status()
        identity = accepted.json()
        async with asyncio.timeout(90):
            while (await client.get('/api/state')).json()['active_turn_id']:
                await asyncio.sleep(.03)
        snapshot = (await client.get('/api/sessions/' + identity['session_id'])).json()
        self.steps.append({'input': text, 'identity': identity, 'answer': snapshot['turns'][-1]['answer'],
                           'preferences_before_idle': (await client.get('/api/preferences')).json()})
        return identity

    async def idle(self, client):
        await self.clock.advance(3600)
        # 其他新会话可能较晚收到输入；模型请求期间不推进重试时钟。
        for delay in (0, 30, 120):
            if delay:
                await self.clock.advance(delay)
            async with asyncio.timeout(90):
                while True:
                    state = (await client.get('/api/preferences')).json()
                    if not any(p['status'] == 'processing' for p in state['processing']):
                        # 调度可能尚未开始，给予真实事件循环一个观察间隔。
                        await asyncio.sleep(.1)
                        state = (await client.get('/api/preferences')).json()
                        if not any(p['status'] == 'processing' for p in state['processing']):
                            break
                    await asyncio.sleep(.03)
            if not state['processing']:
                break
        self.steps[-1]['after_idle'] = state
        return state


DIET = '我长期不吃辣。这里只说明长期口味，不用查询地图或安排具体行程。'
RUNS = {}
CASES = ['four', 'duplicate', 'temporary', 'long_term', 'third_party', 'pace', 'trip', 'rejected_suggestion', 'long_term_negative', 'ambiguity', 'same_batch', 'old_context', 'reference', 'managed_edit', 'managed_delete']


async def evaluate(name, model):
    run = Evaluation(model)
    RUNS[name] = run
    with tempfile.TemporaryDirectory() as directory:
        async with run.service(directory) as client:
            seed = None
            if name in ('duplicate', 'temporary', 'long_term', 'ambiguity', 'managed_edit', 'managed_delete'):
                seed = await run.turn(client, DIET)
                await run.idle(client)
            if name == 'four':
                await run.turn(client, '我长期不吃辣。我喜欢逛博物馆。城市出行我通常优先坐地铁。我住宿一向喜欢安静、远离电梯的房间。这里只说明长期喜好，不用查地图，也不用安排具体行程。')
                # 新会话在保存前不得声称已经形成跨会话记忆。
                await run.turn(client, '我有什么已经保存的长期偏好？不用查询地图。')
                await run.idle(client)
                await run.turn(client, '给我这次旅行选择餐饮、活动、交通和房间的原则。只说原则，不用查地图，不需要具体地点。')
            elif name == 'duplicate':
                await run.turn(client, '我还是一直不吃辣。只讨论口味，不用查询。')
                await run.idle(client)
            elif name == 'temporary':
                await run.turn(client, '这次可以吃一点辣。请按这次的条件给我点餐原则，不用查询。')
                await run.idle(client)
                await run.turn(client, '下次点餐选口味的原则是什么？不查地图。')
            elif name == 'long_term':
                await run.turn(client, '我现在喜欢吃辣了，以后也按这个口味推荐。不用查询。')
                await run.idle(client)
                await run.turn(client, '下次点餐选口味的原则是什么？不查地图。')
            elif name == 'third_party':
                await run.turn(client, '妈妈不吃辣，这次我们一起去。我没有说自己的长期口味。这里只交流，不用查询。')
                await run.idle(client)
            elif name == 'pace':
                await run.turn(client, '今天很累，每天只想玩一个景点，慢慢走。这里只交流本次要求，不用查询。')
                await run.idle(client)
            elif name == 'trip':
                await run.turn(client, '这次11月1日去杭州，预算2000元，已经订了西湖边的酒店。这里只交流本次安排，不用查询。')
                await run.idle(client)
            elif name == 'rejected_suggestion':
                first = await run.turn(client, '请建议一种室内文化活动，只讨论活动类型，不用查询地图。')
                await run.turn(client, '不要按你刚才的博物馆建议安排，这次改去公园。只讨论本次调整，不用查询。', first['session_id'])
                await run.idle(client)
            elif name == 'long_term_negative':
                await run.turn(client, '我一直不喜欢博物馆，长期更喜欢自然公园。只交流长期喜好，不用查询。')
                await run.idle(client)
                await run.turn(client, '活动类型怎么选？只说原则，不用查询。')
            elif name == 'ambiguity':
                assert seed is not None
                await run.turn(client, '那个现在也可以了。只讨论口味，不用查询。', seed['session_id'])
                await run.idle(client)
                # 重启生命周期后，待澄清信息应保持。
            elif name == 'same_batch':
                first = await run.turn(client, DIET)
                await run.turn(client, '我说错了，其实我一直喜欢吃辣。只交流长期喜好，不用查询。', first['session_id'])
                await run.idle(client)
            elif name == 'reference':
                first = await run.turn(client, '请只推荐一种户外活动，用“自然公园徒步”这个活动名称回答，不用查地图，不列其他选项。')
                await run.turn(client, '你刚才说的那个我一直很喜欢。这里只交流长期喜好，不用查询。', first['session_id'])
                await run.idle(client)
                await run.turn(client, '我的已保存长期活动兴趣是什么？只说原则，不用查询。')
            elif name in ('managed_edit', 'managed_delete'):
                await run.turn(client, '我一向避开辣椒，通常坚持不吃有辣味的东西；另外我长期喜欢喝绿茶。这只表达本人长期喜好，不用查地图。')
                original = (await client.get('/api/preferences')).json()['preferences'][0]
                path = '/api/preferences/' + original['id']
                managed = (await client.patch(path, json={'version': original['version'], 'content': '喜欢清淡口味'})
                           if name == 'managed_edit' else await client.delete(path, params={'version': original['version']}))
                managed.raise_for_status()
                run.steps[-1]['management'] = {'operation': name, 'state': managed.json()}
            elif name == 'old_context':
                first = await run.turn(client, '请介绍素食的含义，我没有表达自己的饮食喜好。不用查询。')
                await run.idle(client)
                await run.turn(client, '继续。', first['session_id'])
                await run.idle(client)
            if name in ('duplicate', 'third_party', 'pace', 'trip', 'rejected_suggestion', 'same_batch', 'old_context'):
                await run.turn(client, '仅根据我已经保存的长期偏好，说明以后选择餐饮、活动、交通和住宿时的原则。不用查询，不需要具体地点。')
        if name in ('managed_edit', 'managed_delete'):
            # 已接受而尚未提取的旧表达在管理后跨真实生命周期重启处理。
            async with run.service(directory) as client:
                await run.idle(client)
                await run.turn(client, '我的已保存长期饮食喜好有哪些？只说已有记录，不用查询。')
        if name == 'ambiguity':
            async with run.service(directory) as client:
                await run.turn(client, '杭州东站怎么坐地铁到西湖？只说选择地铁的原则，不用查询具体路线。')
                await run.turn(client, '以后给我推荐餐馆时怎么选口味？只讨论原则，不用查询。')
                await run.turn(client, '这次明确只吃清淡无辣，给我点餐原则，不用查询。')
                clear = await run.turn(client, '刚才我的意思是以后也喜欢吃辣，不只是这次。不用查询。')
                await run.clock.advance(3599)
                run.steps[-1]['before_one_hour'] = (await client.get('/api/preferences')).json()
                await run.clock.advance(1)
                # 等待最终批次；此前无关会话也达到资格。
                async with asyncio.timeout(90):
                    while (await client.get('/api/preferences')).json()['processing']:
                        await asyncio.sleep(.05)
                run.steps[-1]['after_idle'] = (await client.get('/api/preferences')).json()
                await run.turn(client, '下次点餐怎么选口味？只说原则，不用查询。')
    result = {'case': name, 'steps': run.steps, 'requests': run.calls, 'error': None}
    result['storage_checks'] = storage_checks(result)
    return result


def storage_checks(result):
    # 此处只判定公开事实/协议；回答是否自然、针对性仍由记录中的原文人工复核。
    name, steps = result['case'], result['steps']
    states = [s['after_idle'] for s in steps if 'after_idle' in s]
    final = states[-1]
    checks = {'后台处理完成': all(not state['processing'] for state in states),
              '主回答请求成功': not any('error_type' in call for call in result['requests'] if call['purpose'] == 'travel_answer')}
    extraction = [c for c in result['requests'] if c['purpose'] == 'preference_extraction']
    checks['提取完整结束'] = all(c.get('stop_reason') == 'end_turn' for c in extraction)
    sources_valid = True
    for call in extraction:
        try:
            sources = {i['turn_id']: i['input'] for i in call['input']['new_inputs']}
            for change in json.loads(call['text'])['changes']:
                sources_valid &= change['source_turn_id'] in sources and change['evidence'] in sources[change['source_turn_id']]
        except (KeyError, ValueError, TypeError):
            sources_valid = False
    checks['依据来自本批新输入'] = sources_valid
    if name == 'four':
        checks['四类别'] = {p['category'] for p in final['preferences']} == {'diet', 'activity', 'transport', 'lodging'}
        checks['保存前新会话无背景'] = not steps[1]['preferences_before_idle']['preferences']
    elif name == 'duplicate':
        checks['去重及新来源'] = len(final['preferences']) == 1 and final['preferences'][0]['id'] == states[0]['preferences'][0]['id'] and final['preferences'][0]['source_turn_id'] == steps[1]['identity']['turn_id']
    elif name == 'temporary':
        checks['临时例外保留有效记录'] = final['preferences'] == states[0]['preferences'] and not final['ambiguities']
    elif name == 'long_term':
        checks['长期变化更新原条目及来源'] = len(final['preferences']) == 1 and final['preferences'][0]['id'] == states[0]['preferences'][0]['id'] and final['preferences'][0]['source_turn_id'] == steps[1]['identity']['turn_id']
    elif name in ('third_party', 'pace', 'trip', 'rejected_suggestion', 'old_context'):
        checks['排除非本人稳定偏好'] = final['preferences'] == [] and final['ambiguities'] == []
    elif name == 'long_term_negative':
        checks['保存活动兴趣'] = bool(final['preferences']) and all(p['category'] == 'activity' for p in final['preferences'])
    elif name == 'ambiguity':
        checks['歧义保留原值'] = states[1]['preferences'] == states[0]['preferences'] and bool(states[1]['ambiguities'])
        clear = next(s for s in steps if 'before_one_hour' in s)
        checks['澄清仍等待一小时'] = clear['before_one_hour']['preferences'] == states[0]['preferences']
        checks['新来源且清除歧义'] = not final['ambiguities'] and len(final['preferences']) == 1 and final['preferences'][0]['source_turn_id'] == clear['identity']['turn_id']
    elif name == 'reference':
        checks['批内指代有助手上下文且新输入授权'] = bool(final['preferences']) and all(
            p['category'] == 'activity' and p['source_turn_id'] == steps[1]['identity']['turn_id'] for p in final['preferences'])
    elif name in ('managed_edit', 'managed_delete'):
        managed = steps[1]['management']['state']['preferences']
        checks['管理结果保留并新增独立同类别条目'] = all(p in final['preferences'] for p in managed) and len(final['preferences']) == len(managed) + 1
        checks['独立条目来自管理前新表达'] = sum(p['source_turn_id'] == steps[1]['identity']['turn_id'] for p in final['preferences']) == 1
    elif name == 'same_batch':
        checks['仅最终修正且来源较新'] = len(final['preferences']) == 1 and final['preferences'][0]['source_turn_id'] == steps[1]['identity']['turn_id']
    return checks


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=CASES, action='append')
    parser.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[1] / '.env')
    parser.add_argument('--output', default='/tmp/traveler-preferences-semantic.json')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    load_dotenv(args.env_file, override=True)
    model = os.environ['MODEL_ID']
    results = []
    for name in args.case or CASES:
        try:
            result = await evaluate(name, model)
        except Exception as error:
            run = RUNS.get(name)
            result = {'case': name, 'error': type(error).__name__, 'steps': run.steps if run else [], 'requests': run.calls if run else []}
        results.append(result)
        Path(args.output).write_text(json.dumps({'model': model, 'boundary': '公开HTTP+真实模型+临时SQLite+可控时间；地图替身', 'results': results}, ensure_ascii=False, indent=2))
        print(json.dumps({'case': name, 'error': result['error'], 'requests': len(result.get('requests', []))}, ensure_ascii=False), flush=True)
    return 1 if any(r['error'] or not all(r.get('storage_checks', {}).values()) for r in results) else 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
