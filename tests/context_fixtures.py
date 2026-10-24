"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
import json
from service_fixtures import MapService, response
from http_fixtures import wait_finished
from types import SimpleNamespace


ENV = {"CONTEXT_COMPACTION_ENABLED": "true", "MODEL_CONTEXT_WINDOW": "40000"}


OLD = "2026-10-18 杭州旅行，预算3000元，保留西湖酒店。" + "中" * 3900


def summary_response(**changes):
    value = {"travel_conditions": {"budget": "3000元", "hotel": "西湖酒店"},
             "confirmed_itinerary": [], "corrections": [], "unresolved": ["早餐价格待核实"],
             "sources": [{"record_id": "message:1", "verification": "用户要求"},
                         {"record_id": "message:2", "verification": "未预订"}]}
    value.update(changes)
    return response([{"type": "text", "text": json.dumps(value, ensure_ascii=False)}])


async def submit(client, text, session=None):
    accepted = (await client.post("/api/turns", json={"input": text, "session_id": session})).json()
    return await wait_finished(client, accepted["session_id"], accepted["turn_id"])


class LargeMaps(MapService):
    async def call_tool(self, name, arguments):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="来源2026-10-18，价格待核实、未预订。" + "资" * 1600)],
                               structured_content=None, is_error=False)


__all__ = ['ENV', 'OLD', 'summary_response', 'submit', 'LargeMaps']
