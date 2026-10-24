"""既有消息式回归到公开规划入口的测试适配，无业务 implementation 依赖。"""
import json
from travel_app.context import PlanningSeed
from travel_app.planning import plan


async def agent_loop(messages, client, tools, model, **kwargs):
    result = await plan(messages[-1]["content"], client, tools, model,
        seed=PlanningSeed(messages_json=json.dumps(messages[:-1], ensure_ascii=False),
            source_materials_json=json.dumps(kwargs.pop("previous_source_materials", []), ensure_ascii=False)), **kwargs)
    messages[:] = json.loads(result.material.messages_json)
    return result.answer


__all__ = ["agent_loop"]
