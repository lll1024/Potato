"""公开 HTTP 验证旅行日期、已保存偏好和资料降级在合并后共同生效。"""
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast

import httpx
from anthropic import AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import MapService
import test_preferences
from test_preferences import Clock, PreferenceModel
from test_travel_dates import context_from_request
from travel_tools import TravelTools
from web import Runtime, create_app


class IntegratedTravelContextTests(unittest.IsolatedAsyncioTestCase):
    finish = test_preferences.PreferenceTests.finish

    async def test_saved_preferences_and_trip_dates_survive_restart_with_sources_unavailable(self):
        model, idle_clock = PreferenceModel(), Clock()
        moment = datetime.fromisoformat('2026-10-09T09:00:00+08:00')

        @asynccontextmanager
        async def resources():
            amap = AmapTools(cast(Client, MapService()))
            await amap.discover()
            yield Runtime(cast(AsyncAnthropic, model), TravelTools(amap), 'test-model')

        @asynccontextmanager
        async def service(directory):
            app = create_app(directory, resources=resources, clock=lambda: moment,
                             preference_clock=idle_clock, preference_wait=idle_clock.wait)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    yield client

        with tempfile.TemporaryDirectory() as directory:
            async with service(directory) as client:
                await self.finish(client, '我一直不吃辣')
                await idle_clock.advance(3600)
                self.assertEqual([p['content'] for p in (await client.get('/api/preferences')).json()['preferences']], ['不吃辣'])
                trip = await self.finish(client, '本周末去杭州，推荐餐馆')
                first_request = model.requests[-1]
            moment = datetime.fromisoformat('2026-10-12T09:00:00+08:00')
            async with service(directory) as client:
                await self.finish(client, '保留旅行日期，换一家餐馆', trip['session_id'])
                snapshot = (await client.get('/api/sessions/' + trip['session_id'])).json()
                self.assertEqual([p['content'] for p in (await client.get('/api/preferences')).json()['preferences']], ['不吃辣'])

        for request in (first_request, model.requests[-1]):
            dates = context_from_request(request)['travel_dates']
            self.assertEqual((dates['start_date'], dates['end_date']), ('2026-10-10', '2026-10-11'))
            self.assertIn('不吃辣', request['system'])
            self.assertIn('本轮资料服务状态：', request['system'])
            self.assertNotIn('不吃辣', str(request['messages']))
        self.assertEqual(snapshot['turns'][-1]['status'], 'completed')
        self.assertEqual(snapshot['turns'][-1]['travel_date_context']['today'], '2026-10-12')
