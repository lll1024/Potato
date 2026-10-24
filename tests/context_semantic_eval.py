"""显式真实模型压缩对照；HTTP/SQLite/CLI 公开入口，地图为控制替身。

仅从 --env-file 在内存读取配置；证据不包含 endpoint、凭据或异常原文。
--case 支持定向重验。测试窗口小于用户确认的实际 1M 容量，二者分别记录。
"""
import argparse
import asyncio
import copy
import io
import hashlib
import shutil
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from contextlib import asynccontextmanager, redirect_stdout
from datetime import datetime
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from mcp import Client
from travel_app.retrieval import Retrieval
from travel_app.sessions import Runtime, create_app

from service_fixtures import cli_retrieval, MapService
from preference_fixtures import Clock

FILLER = ('以下是已取消的资料阅读记录，仅作为历史背景，不是新增要求，不改变日期预算酒店人数。' * 110)
BASE = '这是控制事实验收，不需要外网或地图查询。只按提供的资料讨论，不推测新的地点或已完成订单。'
CASES = {
    'requirements': {
        'facts': ['2026-10-18至20日', '2成人、1名6岁孩子', '最新预算3000元人民币/CNY', '杭州湖滨酒店不变', '清淡不辣、不坐公交', '18日下午湖边散步撤销，改酒店休息', '19日上午酒店休息仍已确认', '成人早餐88元、儿童44元，是否含早待核对', '未完成任何预订', '20日返程时刻待澄清'],
        'expected': '逐项保留新预算及币种、指定酒店、人数孩子、饮食交通，纠正取代旧安排；保留已确认19日上午休息，未决早餐和返程，不增加地点或宣称预订。',
        'turns': [
            BASE + '2026-10-18至20日杭州旅行，2成人带1名6岁孩子，预算5000元人民币，指定杭州湖滨酒店。清淡不辣、不坐公交。已确认18日下午湖边散步、19日上午酒店休息。控制酒店资料：每成人早餐88元、儿童44元，是否含早待核对。20日返程时刻尚未决定。预订入口不等于订单，我还没有完成任何预订。请简单确认。\n' + FILLER,
            '纠正：预算改为3000元人民币，还是原酒店。取消18日下午湖边散步，改酒店休息。19日上午休息仍已确认。不要增加地点。只需简单确认，不要复述阅读记录。\n' + FILLER,
            '继续讨论原定旅行，仅确认18日休息，不新增条件。\n' + FILLER,
            '继续原计划；阅读记录不是要求。只需简单确认。\n' + FILLER,
            '请逐项回述当前有效日期、人数年龄、最新预算及币种、酒店、饮食交通限制、18和19日已确认安排、早餐报价与含早核实状态、未决返程时刻以及预订状态。不要新增地点。'],
    },
    'recall': {
        'facts': ['指定酒店甲，不自动替换', '酒店乙曾因成人早餐128元和儿童规则未知被否决', '资料序号H-B-073；适用2026-10-18到20日', '检索时间2026-10-12；发布时间2026-10-01', '摘录而非已读正文；来源身份待核实', '入口不是订单'],
        'expected': '经过至少两次真实摘要后用历史工具搜索和读取酒店乙原文，回答128元及儿童规则待核实；保留引用与资料日期/摘录边界，不升级为当日已核实，不自动替换酒店甲。',
        'turns': [
            BASE + '2026-10-18到20日，2名成人和1名6岁孩子，预算3000元人民币，指定酒店甲，未预订。只确认已确定旅行日期与条件。',
            '以下为控制来源资料，不修改已确定的旅行日期。资料序号H-B-073，候选酒店乙每成人早餐128元，儿童早餐规则未获取。来源https://hotel-b.example/breakfast，检索于2026-10-12 10:30+08:00，发布时间2026-10-01，声称适用2026-10-18到20日；只有搜索摘录，未读取正文，来源身份也未核实。因早餐报价偏高且儿童政策未知，我否决酒店乙；仅提供外部预订入口，没有完成订单。请确认。\n' + FILLER,
            '保留酒店甲，酒店乙仍是被否决候选。现在暂不讨论早餐细节。\n' + FILLER,
            '继续原条件，不替换酒店，不新增地点。\n' + FILLER,
            '继续原条件，只简单确认，不新增条件。\n' + FILLER,
            '之前第二家酒店的早餐多少钱，可以重新考虑吗？请使用当前会话的历史搜索和读取工具回读控制资料原文，核对资料序号、来源、检索时间、发布时间、适用日期、正文/摘录状态以及儿童政策。保持我的指定酒店，说明未预订和仍待核实的边界，不进行外网查询。'],
    },
    'dates_preferences_sources': {
        'facts': ['原明天=2026-10-13', '续聊当前时钟2026-10-14且真实服务重启', '稳定偏好由独立管理从不吃辣改为素食', '本次酒店湖滨、预算3000不写长期偏好', '来源A只是搜索摘录', '来源B已读正文但身份待核实且公告仅适用10月13', '不保证10月14开放', '预订未完成'],
        'expected': '重启跨午夜仍保留已定10月13旅行日期；当前轮独立读取管理后的素食偏好，摘要不固定旧快照，不把预算酒店作为长期偏好；分别标注摘录/正文、官方身份和适用日期缺口，不凭旧公告保证开放，不宣称预订。',
        'turns': [
            BASE + '明天去杭州，2成人，预算3000元人民币，指定湖滨酒店。未完成任何预订。请确认旅行日期。',
            '以下是控制资料，仅核对来源，不修改已确定的旅行日期。来源A https://source-a.example/open 是搜索摘录，检索2026-10-12，发布日期未知、适用日期未知，只称通常开放；来源B https://source-b.example/notice 已读取正文，发布2026-10-10，正文仅称2026-10-13开放，发布者官方身份尚未核实。10月14日是否开放没有公告。请确认原定日期与核实边界。',
            '仅确认本次条件不变，不新增要求；不必查询工具或回读下面已取消的阅读背景。\n' + FILLER,
            '仅确认本次条件不变，不新增要求；不必查询工具或回读下面已取消的阅读背景。\n' + FILLER,
            '仅确认本次条件不变，不新增要求；不必查询工具或回读下面已取消的阅读背景。\n' + FILLER,
            '仅确认本次条件不变，不新增要求；不必查询工具或回读下面已取消的阅读背景。\n' + FILLER,
            '请回述已确定的旅行日期、本轮独立保存的长期饮食偏好和当前旅行要求，并逐项比较来源A搜索摘录与来源B正文的可信度、发布时间及适用日期。能保证10月14日开放吗？预订是否已完成？只按历史和当前保存偏好回答，不外查。'],
    },
}


class Evaluation:
    def __init__(self, model, window, enabled):
        self.model, self.window, self.enabled = model, window, enabled
        self.calls, self.steps = [], []
        self.now = datetime.fromisoformat('2026-10-12T23:50:00+08:00')
        self.preference_clock = Clock()

    @asynccontextmanager
    async def service(self, directory):
        @asynccontextmanager
        async def resources():
            async with AsyncAnthropic(api_key=os.environ['ANTHROPIC_API_KEY'], base_url=os.environ['ANTHROPIC_BASE_URL'], max_retries=0, timeout=60) as real:
                owner = self
                class Observed:
                    def __init__(self):
                        self.messages = self
                    async def create(self, **kwargs):
                        purpose = ('context_summary' if kwargs['system'].startswith('整理旅行状态') else 'preference_extraction' if kwargs['system'].startswith('旅行者偏好提取') else 'travel')
                        call = {'purpose': purpose, 'input': copy.deepcopy(kwargs)}
                        owner.calls.append(call)
                        start = time.perf_counter()
                        try:
                            result = await real.messages.create(**kwargs)
                            call['response'] = result.model_dump(mode='json', exclude_unset=True, warnings=False)
                            call['stop_reason'] = result.stop_reason
                            usage = call['response'].get('usage')
                            call['raw_usage'] = usage
                            call['usage_missing'] = [field for field in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens') if not isinstance(usage, dict) or field not in usage]
                            return result
                        except Exception as error:
                            call['error_type'] = type(error).__name__
                            call['status_code'] = getattr(error, 'status_code', None)
                            raise
                        finally:
                            call['duration_ms'] = (time.perf_counter() - start) * 1000
                            print(json.dumps({'actual_request': len(owner.calls), 'purpose': purpose, 'stop_reason': call.get('stop_reason'), 'error_type': call.get('error_type')}, ensure_ascii=False), flush=True, file=sys.stderr)
                tools = Retrieval(cast(Client, MapService()), secrets=tuple(os.environ.get(k, '') for k in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL')))
                await tools.discover()
                self.observed = Observed()
                yield Runtime(cast(AsyncAnthropic, self.observed), tools, self.model)
        app = create_app(directory, resources=resources, clock=lambda: self.now, preference_clock=self.preference_clock, preference_wait=self.preference_clock.wait)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            while not server.started and not task.done():
                await asyncio.sleep(.01)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{sock.getsockname()[1]}', timeout=300) as client:
                yield client
        finally:
            server.should_exit = True
            await task
            sock.close()

    async def turn(self, client, text, session=None):
        start = time.perf_counter()
        accepted = await client.post('/api/turns', json={'input': text, 'session_id': session})
        accepted.raise_for_status()
        identity = accepted.json()
        async with asyncio.timeout(300):
            while (await client.get('/api/state')).json()['active_turn_id']:
                await asyncio.sleep(.05)
        snapshot = (await client.get('/api/sessions/' + identity['session_id'])).json()
        step = {'input': text, 'identity': identity, 'clock': self.now.isoformat(), 'duration_ms': (time.perf_counter() - start) * 1000, 'turn': snapshot['turns'][-1], 'snapshot': snapshot, 'preferences': (await client.get('/api/preferences')).json()}
        self.steps.append(step)
        return identity['session_id']

    async def extract(self, client):
        await self.preference_clock.advance(3600)
        async with asyncio.timeout(120):
            while True:
                state = (await client.get('/api/preferences')).json()
                if not state['processing']:
                    return state
                await asyncio.sleep(.05)


def metrics(calls):
    return {'request_count': len(calls), 'summary_count': sum(c['purpose'] == 'context_summary' for c in calls),
            'total_model_duration_ms': sum(c.get('duration_ms', 0) for c in calls),
            'summary_duration_ms': sum(c.get('duration_ms', 0) for c in calls if c['purpose'] == 'context_summary'),
            'returned_input_tokens': sum((c.get('raw_usage') or {}).get('input_tokens', 0) for c in calls),
            'returned_output_tokens': sum((c.get('raw_usage') or {}).get('output_tokens', 0) for c in calls),
            'missing_usage_request_count': sum(c.get('raw_usage') is None for c in calls),
            'cache_semantics': '兼容服务的实际字段仅原样保留，缺失未知，不按Anthropic缓存计费假设推算',
            'cost': {'value': None, 'status': '不可计算：未提供此部署服务的输入/输出/缓存单价'}}


async def evaluate(name, model, enabled):
    run = Evaluation(model, 1000000 if name == 'low_pressure' else 60000, enabled)
    result = {'case': name, 'group': 'compressed' if enabled else 'uncompressed', 'actual_service_window_confirmed': 1000000, 'explicit_test_window': run.window, 'budget_note': '60000是保守验收窗口，服务实际为用户确认的1000000；对照组禁用压缩', 'error': None, 'steps': run.steps, 'requests': run.calls}
    specification = CASES.get(name, {'facts': ['低压力不产生摘要', '杭州2成人预算3000元人民币、未预订'], 'expected': '实际1M配置下成功回答，无上下文摘要', 'turns': [BASE + '2026-10-18杭州，2成人，预算3000元人民币，未完成预订。仅回述要求，不查询。']})
    result.update({'latest_effective_facts': specification['facts'], 'expected': specification['expected'], 'original_inputs': specification['turns']})
    with patch.dict(os.environ, {'CONTEXT_COMPACTION_ENABLED': 'true' if enabled else 'false', 'MODEL_CONTEXT_WINDOW': str(run.window)}), tempfile.TemporaryDirectory() as directory:
        try:
            session = None
            async with run.service(directory) as client:
                if name == 'dates_preferences_sources':
                    await run.turn(client, '我长期不吃辣。仅确认口味，不安排本次旅行，也不用查询。')
                    result['preference_seed'] = await run.extract(client)
                for text in specification['turns'][:-1]:
                    session = await run.turn(client, text, session)
                if name == 'dates_preferences_sources':
                    preferences = (await client.get('/api/preferences')).json()['preferences']
                    if not preferences:
                        raise AssertionError('稳定偏好未保存')
                    preference = preferences[0]
                    managed = await client.patch('/api/preferences/' + preference['id'], json={'version': preference['version'], 'content': '长期只吃素食'})
                    managed.raise_for_status()
                    result['managed_preferences'] = managed.json()
                else:
                    session = await run.turn(client, specification['turns'][-1], session)
            if name == 'dates_preferences_sources':
                run.now = datetime.fromisoformat('2026-10-14T00:10:00+08:00')
                async with run.service(directory) as client:
                    await run.turn(client, specification['turns'][-1], session)
                    result['preferences_after_idle'] = await run.extract(client)
        except Exception as error:
            result['error'] = type(error).__name__
    result['metrics'] = metrics(run.calls)
    if run.steps:
        result['actual_answer'] = run.steps[-1]['turn']['answer']
        snapshot = run.steps[-1]['snapshot']
        result['checks'] = {'所有公开轮次成功': all(s['turn']['status'] == 'completed' for s in run.steps), '全部实际调用成功': all('error_type' not in c for c in run.calls), '摘要完整结束': all(c.get('stop_reason') == 'end_turn' for c in run.calls if c['purpose'] == 'context_summary'), '原始输入可公开回读': all(s['input'] == s['turn']['input'] for s in run.steps), '低压力无摘要或长会话真实多次摘要': result['metrics']['summary_count'] == 0 if not enabled or name == 'low_pressure' else sum(e['kind'] == 'context.saved' for e in snapshot['events']) >= 2}
    if name == 'dates_preferences_sources' and run.steps and 'checks' in result:
        result['checks'].update({
            '跨午夜重启原旅行日期保留': run.steps[-1]['turn']['travel_date_context']['travel_dates'].get('start_date') == '2026-10-13',
            '管理偏好本轮独立读取': '长期只吃素食' in next(c['input']['system'] for c in reversed(run.calls) if c['purpose'] == 'travel'),
            '本次要求未写偏好且管理结果保留': result.get('preferences_after_idle', {}).get('preferences') == result.get('managed_preferences', {}).get('preferences'),
            '摘要不是偏好提取输入': all('travel_state_summary' not in str(c['input']['messages']) for c in run.calls if c['purpose'] == 'preference_extraction'),
        })
    if name == 'recall' and run.steps and 'checks' in result:
        final_id = run.steps[-1]['identity']['turn_id']
        names = {tool['name'] for tool in run.steps[-1]['snapshot']['tool_calls'] if tool['turn_id'] == final_id}
        result['checks']['真实模型主动搜索并读取原文'] = {'session_history_search', 'session_history_read'} <= names
    return result


async def evaluate_pair(name, model):
    """真实公共入口生成一次种子，复制已关闭SQLite；两个组的完整协议原文一致。"""
    spec = CASES[name]
    seed = Evaluation(model, 60000, False)
    result = {'case': name, 'mode': 'same_original_protocol', 'latest_effective_facts': spec['facts'], 'expected': spec['expected'], 'error': None, 'seed_requests': seed.calls, 'seed_steps': seed.steps, 'groups': []}
    with tempfile.TemporaryDirectory() as root:
        directory = Path(root) / 'seed'
        try:
            with patch.dict(os.environ, {'CONTEXT_COMPACTION_ENABLED': 'false', 'MODEL_CONTEXT_WINDOW': '60000'}):
                async with seed.service(directory) as client:
                    if name == 'dates_preferences_sources':
                        await seed.turn(client, '我长期不吃辣。仅确认口味，不安排本次旅行，也不用查询。')
                        await seed.extract(client)
                    session = None
                    for text in spec['turns'][:-1]:
                        session = await seed.turn(client, text, session)
                    if name == 'dates_preferences_sources':
                        preference = (await client.get('/api/preferences')).json()['preferences'][0]
                        managed = await client.patch('/api/preferences/' + preference['id'], json={'version': preference['version'], 'content': '长期只吃素食'})
                        managed.raise_for_status()
                    original = seed.steps[-1]['turn']['messages']
                    result['original_protocol'] = original
                    result['original_protocol_sha256'] = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    result['seed_preferences'] = (await client.get('/api/preferences')).json()
            for enabled in (True, False):
                run = Evaluation(model, 60000, enabled)
                if name == 'dates_preferences_sources':
                    run.now = datetime.fromisoformat('2026-10-14T00:10:00+08:00')
                run.preference_clock.now = seed.preference_clock.now
                group = {'case': name, 'group': 'compressed' if enabled else 'uncompressed', 'actual_service_window_confirmed': 1000000, 'explicit_test_window': 60000, 'original_protocol_sha256': result['original_protocol_sha256'], 'error': None, 'steps': run.steps, 'requests': run.calls}
                result['groups'].append(group)
                cloned = Path(root) / group['group']
                shutil.copytree(directory, cloned)
                with patch.dict(os.environ, {'CONTEXT_COMPACTION_ENABLED': 'true' if enabled else 'false', 'MODEL_CONTEXT_WINDOW': '60000'}):
                    async with run.service(cloned) as client:
                        resumed = (await client.get('/api/sessions/' + session)).json()['turns'][-1]['messages']
                        group['same_original_protocol_verified'] = resumed == original
                        await run.turn(client, spec['turns'][-1], session)
                group['actual_answer'] = run.steps[-1]['turn']['answer']
                group['metrics'] = metrics(run.calls)
                group['checks'] = {'种子原始协议完全相同': group['same_original_protocol_verified'], '最终公开轮次成功': run.steps[-1]['turn']['status'] == 'completed', '实际请求成功': all('error_type' not in c for c in run.calls), '有真实摘要或对照无摘要': any(c['purpose'] == 'context_summary' for c in run.calls) if enabled else all(c['purpose'] != 'context_summary' for c in run.calls)}
                if name == 'dates_preferences_sources':
                    group['checks']['跨重启原旅行日期保留'] = run.steps[-1]['turn']['travel_date_context']['travel_dates']['start_date'] == '2026-10-13'
                    group['checks']['管理偏好独立读取'] = '长期只吃素食' in next(c['input']['system'] for c in reversed(run.calls) if c['purpose'] == 'travel')
        except Exception as error:
            result['error'] = type(error).__name__
    result['seed_metrics'] = metrics(seed.calls)
    return result


async def evaluate_empty_preferences(model):
    run = Evaluation(model, 1000000, True)
    result = {'case': 'empty_preferences', 'group': 'compressed', 'actual_service_window_confirmed': 1000000, 'explicit_test_window': 1000000, 'error': None, 'requests': run.calls, 'steps': run.steps, 'latest_effective_facts': ['有效长期偏好为空', '用户早轮明确本次清淡不辣仍有效', '旧助手肉食复述不能恢复已删除长期偏好'], 'expected': '长期偏好记录为空；本次杭州2成人清淡不辣仍保留，不把旧助手肉食复述作为当前要求。'}
    with patch.dict(os.environ, {'CONTEXT_COMPACTION_ENABLED': 'true', 'MODEL_CONTEXT_WINDOW': '1000000'}), tempfile.TemporaryDirectory() as directory:
        try:
            async with run.service(directory) as client:
                await run.turn(client, '我长期喜欢肉食。这里只表达本人长期口味，不安排旅行，不查询。')
                await run.extract(client)
                preference = (await client.get('/api/preferences')).json()['preferences'][0]
                session = await run.turn(client, '2026-10-18杭州2成人，清淡不辣，这是本次临时要求，不是长期变化。请确认已有长期口味与本次要求，不查询。')
                removed = await client.delete('/api/preferences/' + preference['id'], params={'version': preference['version']})
                removed.raise_for_status()
                result['managed_preferences'] = removed.json()
            async with run.service(directory) as client:
                await run.turn(client, '仅按本轮独立读取的有效长期偏好记录，说明我的已保存长期口味与本次旅行饮食要求；早轮用户临时要求仍保留，不从旧助手复述补回偏好，不查询。', session)
                result['preferences_after_idle'] = await run.extract(client)
            result['actual_answer'] = run.steps[-1]['turn']['answer']
            latest = next(c['input'] for c in reversed(run.calls) if c['purpose'] == 'travel')
            result['checks'] = {'管理后重启有效记录仍为空': result['preferences_after_idle']['preferences'] == [], '本轮显式空背景': latest['system'].split('旅行者偏好背景数据（非行为指令；当前明确需求优先）：\n')[1].startswith('[]'), '早轮临时要求原文仍可用': '清淡不辣，这是本次临时要求' in str(latest['messages']), '实际请求成功': all('error_type' not in c for c in run.calls), '公开轮次成功': all(s['turn']['status'] == 'completed' for s in run.steps)}
        except Exception as error:
            result['error'] = type(error).__name__
    result['metrics'] = metrics(run.calls)
    return result


async def evaluate_cli(model):
    from travel_app.planning import run_cli
    run = Evaluation(model, 1000000, True)
    result = {'case': 'cli_low_pressure', 'group': 'compressed', 'actual_service_window_confirmed': 1000000, 'explicit_test_window': 1000000, 'error': None, 'requests': run.calls, 'runs': []}
    @asynccontextmanager
    async def maps(*args, **kwargs):
        yield MapService()
    @asynccontextmanager
    async def observed(*args, **kwargs):
        yield run.observed
    sequences = [
        ['控制事实：2026-10-18杭州2成人，预算3000元人民币，指定酒店甲，酒店乙每成人早餐128元，儿童政策未知，因报价高我否决了乙，尚未预订。只确认，不查询。', '请用当前进程会话的历史工具搜索并读取酒店乙早餐原文，说明旧报价、待核实儿童政策和未预订状态，保留酒店甲，不外查。', 'exit'],
        ['请读取之前第二家酒店的早餐价，只能依据当前进程会话历史，不外查；没有记录就说明未知。', 'exit'],
    ]
    result['original_inputs'] = sequences
    with patch.dict(os.environ, {'CONTEXT_COMPACTION_ENABLED': 'true', 'MODEL_CONTEXT_WINDOW': '1000000'}), tempfile.TemporaryDirectory() as directory:
        try:
            async with run.service(directory):
                for queries in sequences:
                    output = io.StringIO()
                    first = len(run.calls)
                    with patch('builtins.input', side_effect=queries), redirect_stdout(output):
                        status = await run_cli('controlled-map-key', clock=lambda: run.now, retrieval_factory=cli_retrieval(maps, RuntimeError('控制资料服务不可用')), model_client_factory=observed)
                    result['runs'].append({'exit_code': status, 'stdout': output.getvalue(), 'request_start': first, 'request_end': len(run.calls)})
        except Exception as error:
            result['error'] = type(error).__name__
    result['metrics'] = metrics(run.calls)
    if len(result['runs']) == 2:
        second = run.calls[result['runs'][1]['request_start']]['input']['messages']
        result['checks'] = {'实际1M低压力无摘要': result['metrics']['summary_count'] == 0, '两个CLI调用成功': all(r['exit_code'] == 0 for r in result['runs']), '新CLI生命周期无旧协议': second == [{'role': 'user', 'content': sequences[1][0]}], '实际调用成功': all('error_type' not in c for c in run.calls)}
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=[*CASES, 'low_pressure', 'cli_low_pressure', 'empty_preferences'], action='append')
    parser.add_argument('--paired', action='store_true', help='同一真实种子协议历史的严格对照，仅旅行质量三例')
    parser.add_argument('--group', choices=['compressed', 'uncompressed', 'both'], default='both')
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    load_dotenv(args.env_file, override=True)
    model = os.environ['MODEL_ID']
    results = []
    for name in args.case or [*CASES, 'low_pressure']:
        for enabled in ([True, False] if not args.paired and args.group == 'both' and name not in ('low_pressure', 'cli_low_pressure', 'empty_preferences') else [args.group != 'uncompressed']):
            result = await evaluate_pair(name, model) if args.paired and name in CASES else await evaluate_empty_preferences(model) if name == 'empty_preferences' else await evaluate_cli(model) if name == 'cli_low_pressure' else await evaluate(name, model, enabled)
            results.append(result)
            evidence = {'model': model, 'boundary': '真实HTTP/SSE服务+SQLite+真实当前模型；地图替身；来源资料为明确控制事实；语义由实际回答人工评估', 'results': results}
            raw = json.dumps(evidence, ensure_ascii=False, indent=2)
            for field in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'AMAP_MAPS_API_KEY'):
                secret = os.environ.get(field, '')
                if secret:
                    raw = raw.replace(secret, '[REDACTED]')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(raw)
            print(json.dumps({'case': name, 'group': result.get('group', 'same_original_protocol'), 'error': result['error'], 'checks': result.get('checks'), 'metrics': result.get('metrics', result.get('seed_metrics'))}, ensure_ascii=False), flush=True)
    return 1 if any(r['error'] or not all(r.get('checks', {}).values()) or any(not all(g.get('checks', {}).values()) for g in r.get('groups', [])) for r in results) else 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
