"""通过公开 HTTP/SSE 核对异常资料、原始轨迹与新轮次提交。"""
import asyncio
import json
import tempfile
import unittest

import httpx2
from anthropic import APIConnectionError

from test_agent import ModelService, response
from test_itinerary import ItineraryMapService, map_result
from test_tool_trace import serving_tools
from test_trace import wait_finished


def call(identity, name, arguments):
    return {"type": "tool_use", "id": identity, "name": name, "input": arguments}


class FailingAfterTools(ModelService):
    async def create(self, **kwargs):
        if len(self.requests) == 1:
            self.requests.append(kwargs)
            raise APIConnectionError(request=httpx2.Request("POST", "https://model.example.test"))
        return await super().create(**kwargs)


class ExceptionExperienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidates_routes_and_unreadable_returns_do_not_invent_fields(self):
        calls = [call("candidates", "maps_text_search", {"keywords": "海角", "city": "三亚"}),
            call("route", "maps_direction_walking", {"origin": "1,2", "destination": "3,4"}),
            call("unknown", "maps_weather", {"city": "三亚"}),
            call("failed", "maps_weather", {"city": "三亚"})]
        maps = ItineraryMapService([map_result({"pois": [{"name": "同名海角"}, {"name": "同名海角", "address": "另一地址"}]}),
            map_result({"origin": "1,2", "destination": "3,4", "paths": [{"duration": 0, "distance": "120"}]}),
            map_result(text_only=True, data="无法解析的返回"), map_result({"city": "失败城市"}, error=True)])
        model = FailingAfterTools([response(calls, "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory, model, maps) as client:
                accepted = (await client.post("/api/turns", json={"input": "2027年2月6日去三亚"})).json()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                turn = saved["turns"][0]
                materials = turn["query_materials"]
                self.assertEqual(len(materials), 2)
                self.assertEqual(materials[0]["entries"][0], ["名称：同名海角"])
                self.assertIn("尚未核实详情", materials[0]["title"])
                self.assertIn("耗时（秒）：0", materials[1]["entries"][0])
                self.assertIn("起点：1,2", materials[1]["entries"][0])
                self.assertNotIn("失败城市", str(materials))
                self.assertEqual(turn["unreadable_query_count"], 1)
                self.assertEqual(len(model.requests), 2)

    async def test_explicit_travel_dates_outside_forecast_are_not_covered(self):
        maps = ItineraryMapService([map_result({"forecasts": [{"city": "三亚", "casts": [
            {"date": "2026-10-09", "dayweather": "晴"}]}]})])
        model = FailingAfterTools([response([call("weather", "maps_weather", {"city": "三亚"})], "tool_use")])
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory, model, maps) as client:
                accepted = (await client.post("/api/turns", json={"input": "2027年2月6日到三亚"})).json()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 3)
                materials = saved["turns"][0]["query_materials"]
                self.assertIn("城市：三亚", str(materials))
                self.assertIn("超出", str(materials))
                self.assertIn("不能用于对应日期", str(materials))

    async def test_saved_materials_are_readable_and_retry_keeps_failed_round(self):
        model = FailingAfterTools([
            response([call("place", "maps_search_detail", {"id": "place-id"}),
                      call("weather", "maps_weather", {"city": "三亚"})], "tool_use"),
            response([{"type": "text", "text": "请确认春节年份。"}]),
        ])
        weather = {"city": "三亚", "forecasts": [
            {"date": f"2026-10-{day:02d}", "dayweather": "晴"} for day in range(9, 13)]}
        maps = ItineraryMapService([map_result({"name": "天涯海角", "address": "三亚市天涯区",
            "opentime": "08:00-18:00"}), map_result(weather)])
        with tempfile.TemporaryDirectory() as directory:
            async with serving_tools(directory, model, maps) as client:
                original = "春节正月初一到初三去三亚"
                accepted = (await client.post("/api/turns", json={"input": original})).json()
                saved = await asyncio.wait_for(wait_finished(client, accepted["session_id"]), 5)
                turn = saved["turns"][0]
                self.assertEqual(turn["reason"], "model_error")
                self.assertNotIn("maps_search_detail", turn["answer"])
                self.assertNotIn("MODEL_ID", turn["answer"])
                materials = turn["query_materials"]
                self.assertEqual(materials[0]["title"], "地点详情")
                self.assertIn("三亚市天涯区", str(materials[0]))
                self.assertIn("当日", str(materials[0]))
                self.assertIn("2026-10-09", str(materials[1]))
                self.assertIn("2026-10-12", str(materials[1]))
                self.assertIn("尚无法确认", str(materials[1]))
                self.assertNotIn("2027", str(materials))
                for material in materials:
                    raw = (await client.get("/api/payloads/" + material["payload_id"])).json()
                    self.assertFalse(raw["content"]["is_error"])
                await client.get("/api/sessions/" + accepted["session_id"])
                self.assertEqual(len(model.requests), 2)
                self.assertEqual(len(maps.calls), 2)
                retry = {"input": original, "session_id": accepted["session_id"], "submission_id": "retry-once"}
                next_turn = (await client.post("/api/turns", json=retry)).json()
                finished = await asyncio.wait_for(wait_finished(client, accepted["session_id"], next_turn["turn_id"]), 3)
                duplicate = (await client.post("/api/turns", json=retry)).json()
                self.assertEqual(duplicate["turn_id"], next_turn["turn_id"])
                self.assertNotEqual(turn["turn_id"], next_turn["turn_id"])
                self.assertEqual(finished["turns"][0], turn)
                self.assertEqual(finished["turns"][1]["input"], original)
                self.assertEqual(len(model.requests), 3)
                self.assertIn("tool_result", json.dumps(model.requests[-1]["messages"]))
                self.assertEqual(finished["tool_calls"], saved["tool_calls"])
