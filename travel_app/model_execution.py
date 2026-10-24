"""实际模型调用的共同事实采集；预算、重试与摘要采用由消费方决定。"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from travel_app.model_retry import EmptyModelResponse, model_failure


@dataclass(frozen=True)
class ModelReturn:
    response: Any
    parsed: dict[str, Any] | None
    failure: dict[str, Any] | None
    finished_at: str
    duration_ms: float

    def facts(self) -> dict[str, Any]:
        usage = self.parsed.get("usage") if self.parsed is not None else None
        return {"finished_at": self.finished_at, "duration_ms": self.duration_ms,
                "usage": usage, "usage_state": "returned" if usage is not None else "not_returned"}


async def request_model(client, parameters, *, redact: Callable[[str], str], validate=None, empty_message="模型返回空响应。") -> ModelReturn:
    start = asyncio.get_running_loop().time()
    parsed, response, failure = None, None, None
    duration_ms = None
    finished_at = datetime.now(timezone.utc).isoformat()
    stage = "model_call"
    try:
        response = await client.messages.create(**parameters)
        duration_ms = max(0, (asyncio.get_running_loop().time() - start) * 1000)
        finished_at = datetime.now(timezone.utc).isoformat()
        stage = "response_processing"
        parsed = response.model_dump(mode="json", exclude_unset=True, warnings=False)
        service_request_id = getattr(response, "_request_id", None)
        if service_request_id is not None:
            parsed["_request_id"] = service_request_id
        if not response.content:
            raise EmptyModelResponse(empty_message)
        if validate:
            validate(response)
        response = response.model_copy(update={"content": [type(block).model_validate_json(
            redact(block.model_dump_json(warnings=False))) for block in response.content]})
    except Exception as error:
        if duration_ms is None:
            duration_ms = max(0, (asyncio.get_running_loop().time() - start) * 1000)
            finished_at = datetime.now(timezone.utc).isoformat()
        failure = model_failure(error, stage)
    return ModelReturn(response, parsed, failure, finished_at, duration_ms)


__all__ = ["ModelReturn", "request_model"]
