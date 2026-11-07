"""实际模型调用的共同事实采集；预算、重试与摘要采用由消费方决定。"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from travel_app.model_retry import EmptyModelResponse, IncompleteModelResponse, model_failure


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


async def request_model(client, parameters, *, redact: Callable[[str], str], validate=None, empty_message="模型返回空响应。", on_text=None) -> ModelReturn:
    start = asyncio.get_running_loop().time()
    parsed, response, failure = None, None, None
    duration_ms = None
    finished_at = datetime.now(timezone.utc).isoformat()
    stage = "model_call"
    try:
        if on_text is None:
            response = await client.messages.create(**parameters)
        else:
            async with client.messages.stream(**parameters) as stream:
                text_blocks = 0
                in_text_block = False
                received_event = False
                async for event in stream:
                    received_event = True
                    if event.type == "content_block_start":
                        in_text_block = event.content_block.type == "text"
                        if in_text_block:
                            if text_blocks:
                                await on_text("\n")
                            text_blocks += 1
                            if event.content_block.text:
                                await on_text(event.content_block.text)
                    elif event.type == "content_block_delta" and in_text_block and event.delta.type == "text_delta":
                        await on_text(event.delta.text)
                if not received_event:
                    raise EmptyModelResponse(empty_message)
                response = await stream.get_final_message()
        duration_ms = max(0, (asyncio.get_running_loop().time() - start) * 1000)
        finished_at = datetime.now(timezone.utc).isoformat()
        stage = "response_processing"
        if response is None:
            raise EmptyModelResponse(empty_message)
        parsed = response.model_dump(mode="json", exclude_unset=True, warnings=False)
        service_request_id = getattr(response, "_request_id", None)
        if service_request_id is not None:
            parsed["_request_id"] = service_request_id
        if on_text is not None and response.stop_reason is None:
            raise IncompleteModelResponse("模型正文连接中断，未收到完整结束信息。")
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
        failure = model_failure(error, stage, streaming=on_text is not None)
    return ModelReturn(response, parsed, failure, finished_at, duration_ms)


__all__ = ["ModelReturn", "request_model"]
