"""公开 HTTP 每轮重启，验证日期回指与用户澄清/改天数。"""
import tempfile
import unittest
from datetime import datetime

from test_agent import ModelService, response
import test_travel_date_continuation
from test_travel_dates import context_from_request


class TravelDateCorrectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = test_travel_date_continuation.TravelDateContinuationTests()

    finish = test_travel_date_continuation.TravelDateContinuationTests.finish

    async def conversation(self, turns):
        model = ModelService([response([{"type": "text", "text": "协议占位回答"}]) for _ in turns])
        with tempfile.TemporaryDirectory() as directory:
            session = None
            saved = {}
            for now, text in turns:
                self.http.moment = datetime.fromisoformat(now)
                async with self.http.service(directory, model) as client:
                    accepted, saved = await self.finish(client, text, session)
                    session = accepted["session_id"]
        return saved, model

    async def test_reference_to_previously_planned_tomorrow_does_not_move_trip_after_restart(self):
        for text in (
            "之前定的明天去杭州西湖安排不变，只换午餐餐馆",
            "原定明天去杭州西湖安排不变，只换餐馆",
            "之前定明天去杭州西湖安排不变，只换餐馆",
        ):
            with self.subTest(text=text):
                saved, model = await self.conversation([
                    ("2026-10-09T09:00:00+08:00", "明天去杭州西湖"),
                    ("2026-10-10T09:00:00+08:00", text),
                ])
                context = context_from_request(model.requests[-1])
                self.assertEqual(context["today"], "2026-10-10")
                self.assertEqual((context["travel_dates"]["start_date"], context["travel_dates"]["end_date"]),
                                 ("2026-10-10", "2026-10-10"))
                self.assertEqual(context["travel_dates"]["reference_time"], "2026-10-09T09:00:00+08:00")
                self.assertNotIn("dates_changed", context)
                self.assertEqual(saved["turns"][-1]["travel_date_context"], context)

    async def test_calendar_day_followed_by_touring_is_one_day_not_ten_days(self):
        for text in ("2026年10月10日游览故宫", "10月10日游玩故宫", "2026-10-10日游览故宫"):
            with self.subTest(text=text):
                saved, model = await self.conversation([("2026-10-09T09:00:00+08:00", text)])
                dates = context_from_request(model.requests[0])["travel_dates"]
                self.assertEqual((dates["start_date"], dates["end_date"]), ("2026-10-10", "2026-10-10"))
                self.assertEqual(saved["turns"][0]["travel_date_context"]["travel_dates"], dates)

    async def test_one_day_clarification_resolves_original_sunday_after_restart(self):
        for reply in ("那改成一日游", "一日游"):
            with self.subTest(reply=reply):
                saved, model = await self.conversation([
                    ("2026-10-11T09:00:00+08:00", "本周末去北京两日游"),
                    ("2026-10-12T09:00:00+08:00", reply),
                ])
                self.assertEqual(saved["turns"][0]["travel_date_context"]["travel_dates"]["status"], "needs_clarification")
                context = context_from_request(model.requests[-1])
                dates = context["travel_dates"]
                self.assertEqual(dates["status"], "resolved")
                self.assertEqual((dates["start_date"], dates["end_date"]), ("2026-10-11", "2026-10-11"))
                self.assertEqual(dates["reference_time"], "2026-10-11T09:00:00+08:00")
                self.assertEqual(context["today"], "2026-10-12")
                self.assertEqual(saved["turns"][-1]["travel_date_context"], context)

    async def test_duration_change_updates_end_date_and_weather_range_after_restart(self):
        from test_exception_experience import FailingAfterTools
        from test_itinerary import ItineraryMapService, map_result

        initial = ModelService([response([{"type": "text", "text": "协议占位回答"}])])
        changed = FailingAfterTools([response([{"type": "tool_use", "id": "weather", "name": "maps_weather",
                                               "input": {"city": "杭州"}}], "tool_use")])
        maps = ItineraryMapService([map_result({"city": "杭州", "forecasts": [
            {"date": "2026-10-10", "dayweather": "晴"}, {"date": "2026-10-11", "dayweather": "多云"}]})])
        with tempfile.TemporaryDirectory() as directory:
            self.http.moment = datetime.fromisoformat("2026-10-09T09:00:00+08:00")
            async with self.http.service(directory, initial) as client:
                first, original = await self.finish(client, "明天去杭州西湖")
            self.http.moment = datetime.fromisoformat("2026-10-10T09:00:00+08:00")
            async with self.http.service(directory, changed, maps) as client:
                _, saved = await self.finish(client, "改成两日游，起始日期不变", first["session_id"])
        context = context_from_request(changed.requests[0])
        self.assertEqual((context["travel_dates"]["start_date"], context["travel_dates"]["end_date"]),
                         ("2026-10-10", "2026-10-11"))
        self.assertTrue(context["dates_changed"])
        self.assertEqual(context["previous_travel_dates"]["end_date"], "2026-10-10")
        self.assertEqual(context["travel_dates"]["reference_time"], "2026-10-09T09:00:00+08:00")
        turn = saved["turns"][-1]
        self.assertEqual(turn["travel_date_context"], context)
        self.assertEqual(saved["turns"][0], original["turns"][0])
        self.assertIn("覆盖已确定的旅行日期：2026-10-10 至 2026-10-11", str(turn["query_materials"]))

    async def test_explicit_rescheduling_takes_priority_over_historical_relative_reference(self):
        saved, model = await self.conversation([
            ("2026-10-09T09:00:00+08:00", "明天去杭州西湖"),
            ("2026-10-10T09:00:00+08:00", "原定明天去杭州西湖，现在改到下周末"),
        ])
        context = context_from_request(model.requests[-1])
        self.assertEqual(context["travel_dates"]["status"], "resolved")
        self.assertEqual((context["travel_dates"]["start_date"], context["travel_dates"]["end_date"]),
                         ("2026-10-17", "2026-10-18"))
        self.assertTrue(context["dates_changed"])
        self.assertEqual(saved["turns"][-1]["travel_date_context"], context)

    async def test_unrelated_past_trip_does_not_hide_this_trips_new_departure(self):
        saved, model = await self.conversation([
            ("2026-10-09T09:00:00+08:00", "明天去杭州西湖"),
            ("2026-10-10T09:01:00+08:00", "之前去过北京，这次下周末去杭州西湖"),
        ])
        context = context_from_request(model.requests[-1])
        self.assertEqual((context["travel_dates"]["start_date"], context["travel_dates"]["end_date"]),
                         ("2026-10-17", "2026-10-18"))
        self.assertTrue(context["dates_changed"])
        self.assertEqual(context["previous_travel_dates"]["end_date"], "2026-10-10")
        self.assertEqual(saved["turns"][-1]["travel_date_context"], context)

    async def test_source_dates_and_opening_question_do_not_reschedule_after_restart(self):
        saved, model = await self.conversation([
            ("2026-10-12T23:50:00+08:00", "明天去杭州"),
            ("2026-10-13T00:10:00+08:00", "来源A检索2026-10-12，发布日期未知；来源B正文发布2026-10-10，仅适用2026-10-13。旅行安排不变。"),
            ("2026-10-14T00:10:00+08:00", "请回述已确定的旅行日期。来源B能保证10月14日开放吗？"),
            ("2026-10-14T00:15:00+08:00", "旅行日期改到2026年10月18日至20日，核对公告"),
        ])
        for request, turn in zip(model.requests[:3], saved["turns"][:3]):
            context = context_from_request(request)
            self.assertEqual(context["travel_dates"]["start_date"], "2026-10-13")
            self.assertEqual(context["travel_dates"]["end_date"], "2026-10-13")
            self.assertNotIn("dates_changed", context)
            self.assertEqual(turn["travel_date_context"], context)
        changed = context_from_request(model.requests[-1])
        self.assertEqual(changed["travel_dates"]["start_date"], "2026-10-18")
        self.assertEqual(changed["travel_dates"]["end_date"], "2026-10-20")
        self.assertTrue(changed["dates_changed"])

    async def test_new_travel_date_with_opening_question_updates_after_restart(self):
        for activity in ("游览", "游玩", "旅行", "旅游", "出行"):
            with self.subTest(activity=activity):
                saved, model = await self.conversation([
                    ("2026-10-12T23:50:00+08:00", "明天去杭州"),
                    ("2026-10-14T00:10:00+08:00", f"我们2026年10月18日在杭州{activity}，请查景区开放时间"),
                ])
                context = context_from_request(model.requests[-1])
                self.assertEqual((context["travel_dates"]["start_date"], context["travel_dates"]["end_date"]),
                                 ("2026-10-18", "2026-10-18"))
                self.assertTrue(context["dates_changed"])
                self.assertEqual(context["previous_travel_dates"]["start_date"], "2026-10-13")
                self.assertEqual(saved["turns"][-1]["travel_date_context"], context)

    async def test_explicit_departure_and_date_clarification_still_update_with_source_questions(self):
        for reply in ("改成2026年10月14日，核对来源公告", "2026年10月14日出发，核对开放公告",
                      "出发日期确定为2026年10月14日，请核对开放公告", "旅行日期是2026年10月14日，查开放情况",
                      "出发日期确定为2026/10/14，请核对开放公告"):
            with self.subTest(reply=reply):
                saved, model = await self.conversation([
                    ("2026-10-12T23:50:00+08:00", "明天去杭州"),
                    ("2026-10-14T00:10:00+08:00", reply),
                ])
                context = context_from_request(model.requests[-1])
                self.assertEqual(context["travel_dates"]["start_date"], "2026-10-14")
                self.assertTrue(context["dates_changed"])
                self.assertEqual(saved["turns"][-1]["travel_date_context"], context)
        saved, model = await self.conversation([
            ("2026-10-12T23:50:00+08:00", "2026年10月13日或2026年10月14日去杭州"),
            ("2026-10-14T00:10:00+08:00", "2026年10月14日"),
        ])
        self.assertEqual(saved["turns"][0]["travel_date_context"]["travel_dates"]["status"], "needs_clarification")
        self.assertEqual(context_from_request(model.requests[-1])["travel_dates"]["start_date"], "2026-10-14")
