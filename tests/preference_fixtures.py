"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
from contextlib import asynccontextmanager
from typing import cast
import asyncio
import unittest
import copy
import httpx
import json
from anthropic import AsyncAnthropic
from mcp import Client
from travel_app.retrieval import Retrieval
from travel_app.sessions import Runtime, create_app
from service_fixtures import MapService, response


class Clock:
    def __init__(self):
        self.now = 10000.0
        self.wakeup = asyncio.Event()

    def __call__(self):
        return self.now

    async def wait(self, delay):
        await self.wakeup.wait()
        self.wakeup.clear()

    async def advance(self, seconds):
        self.now += seconds
        self.wakeup.set()
        for _ in range(20):
            await asyncio.sleep(0)


class PreferenceModel:
    def __init__(self):
        self.messages = self
        self.requests = []
        self.extract = lambda inputs: {'changes': [{'operation': 'add', 'category': 'diet', 'content': '不吃辣',
            'source_turn_id': inputs[0]['turn_id'], 'evidence': '不吃辣'}]}

    async def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        if kwargs['system'].startswith('旅行者偏好提取'):
            inputs = json.loads(kwargs['messages'][0]['content'])['new_inputs']
            return response([{'type': 'text', 'text': json.dumps(self.extract(inputs), ensure_ascii=False)}])
        return response([{'type': 'text', 'text': '可以继续规划。'}])


class ChangesModel(PreferenceModel):
    def __init__(self):
        super().__init__()
        self.suggest = lambda payload: {'changes': []}

    async def create(self, **kwargs):
        if kwargs['system'].startswith('旅行者偏好提取'):
            self.requests.append(kwargs)
            payload = json.loads(kwargs['messages'][0]['content'])
            return response([{'type': 'text', 'text': json.dumps(self.suggest(payload), ensure_ascii=False)}])
        return await super().create(**kwargs)


def add(payload, content='不吃辣', evidence='不吃辣', index=0):
    return {'operation': 'add', 'category': 'diet', 'content': content,
            'source_turn_id': payload['new_inputs'][index]['turn_id'], 'evidence': evidence}


def targeted(payload, operation, content, evidence, index=0):
    target = payload['existing_preferences'][0]
    return {**add(payload, content, evidence, index), 'operation': operation,
            'target_id': target['id'], 'target_version': target['version']}


class PreferenceHarness(unittest.TestCase):
    @asynccontextmanager
    async def service(self, directory, model, clock, **options):
        @asynccontextmanager
        async def resources():
            tools = Retrieval(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, model), tools, 'test-model')
        app = create_app(directory, resources=resources, preference_clock=clock, preference_wait=clock.wait, **options)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                yield client


    async def finish(self, client, text, session_id=None):
        accepted = await client.post('/api/turns', json={'input': text, 'session_id': session_id})
        self.assertEqual(accepted.status_code, 202)
        identity = accepted.json()
        for _ in range(100):
            if (await client.get('/api/state')).json()['active_turn_id'] is None:
                return identity
            await asyncio.sleep(0)
        self.fail('对话轮次未结束')


__all__ = ['Clock', 'PreferenceModel', 'ChangesModel', 'add', 'targeted', 'PreferenceHarness']
