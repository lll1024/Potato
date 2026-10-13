"""模型调用错误与当前步骤的有限重试策略。"""
import random
import re
import math
import time
from email.utils import parsedate_to_datetime
from typing import Any

from anthropic import APIConnectionError, APIStatusError, APITimeoutError

MAX_RETRIES = 5
RETRYABLE_CODES = {"TRANSPORT", "TIMEOUT", "RATE_LIMIT", "SERVER", "EMPTY_RESPONSE"}


class EmptyModelResponse(RuntimeError):
    """服务已返回，但没有任何可提交的内容块。"""


def failure_code(error: Exception, stage: str) -> str:
    if isinstance(error, EmptyModelResponse):
        return "EMPTY_RESPONSE"
    if stage != "model_call":
        return "UNKNOWN"
    if isinstance(error, APITimeoutError):
        return "TIMEOUT"
    if isinstance(error, APIConnectionError):
        return "TRANSPORT"
    if not isinstance(error, APIStatusError):
        return "UNKNOWN"
    status = error.status_code
    body = error.body if isinstance(error.body, dict) else {}
    detail = body.get("error", body)
    detail = detail if isinstance(detail, dict) else {}
    kind = detail.get("type")
    text = " ".join(value for field in ("code", "type", "message")
                    if isinstance(value := detail.get(field), str))
    if status in (401, 403) or kind in ("authentication_error", "permission_error"):
        return "AUTH"
    if status == 402 or re.search(
        r"\binsufficient[\s_-]+(?:quota|balance|credits?)\b|"
        r"\b(?:quota|usage[\s_-]+limit)[\s_-]+(?:exceeded|exhausted|reached)\b|"
        r"\bexceed(?:ed|s)?[\s_-]+(?:(?:your|the)[\s_-]+)?(?:current[\s_-]+)?quota\b|"
        r"\b(?:balance|credits?)[\s_-]+(?:exhausted|depleted)\b|\bout[\s_-]+of[\s_-]+(?:credits?|budget)\b", text, re.I):
        return "QUOTA"
    if status == 429 or kind == "rate_limit_error":
        return "RATE_LIMIT"
    if re.search(r"\bcontext[\s_-](?:window|length)[\s_-](?:exceed(?:ed|s)?|overflow(?:ed)?|limit[\s_-]exceeded)\b|"
                 r"\b(?:maximum|max)(?:\s+(?:allowed|supported))?\s+context\s+(?:length|window)\b|"
                 r"\b(?:request|prompt|input|messages?)\s+(?:is\s+|are\s+)?too\s+(?:large|long)\s+for\s+(?:(?:this|the)\s+)?(?:model(?:'s)?\s+)?context(?:\s+window)?\b|"
                 r"\b(?:input|prompt|request|messages?)\b.{0,40}\b(?:exceed(?:s|ed)?|overflows?|is\s+larger\s+than)\b.{0,40}\b(?:the\s+)?(?:model(?:'s)?\s+)?context(?:\s+(?:length|window))?\b|"
                 r"\b(?:input|prompt|request)\s+(?:is\s+)?too\s+(?:long|large)\s+for\s+(?:this|the)\s+model\b", text, re.I):
        return "CONTEXT_WINDOW_EXCEEDED"
    if status in (400, 413) or kind == "invalid_request_error":
        return "INVALID_REQUEST"
    if status >= 500 or kind in ("api_error", "overloaded_error"):
        return "SERVER"
    return f"HTTP_{status}"


def model_failure(error: Exception, stage: str) -> dict[str, Any]:
    failure: dict[str, Any] = {"stage": stage, "category": type(error).__name__, "message": str(error),
               "code": failure_code(error, stage)}
    for field in ("status_code", "request_id", "body"):
        value = getattr(error, field, None)
        if value is not None:
            failure[field] = value
    if failure["code"] == "AUTH":
        failure["message"] = "API 密钥无效或无权访问模型。"
        failure["body"] = {"error": {"type": "authentication_error", "message": failure["message"]}}
    else:
        causes = []
        seen = {id(error)}
        cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            causes.append({"category": type(cause).__name__, "message": str(cause)})
            cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
        if causes:
            failure["causes"] = causes
    if isinstance(error, APIStatusError):
        request_id = (error.response.headers.get("request-id") or error.response.headers.get("x-request-id")
                      or error.response.headers.get("x-deepseek-request-id"))
        if request_id:
            failure["request_id"] = request_id
        retry = error.response.headers.get("retry-after")
        if retry is not None:
            failure["retry_after"] = retry
            try:
                delay = float(retry) if re.fullmatch(r"\d+(?:\.\d+)?", retry) else parsedate_to_datetime(retry).timestamp() - time.time()
                if math.isfinite(delay) and delay > 0:
                    failure["retry_after_ms"] = delay * 1000
            except (ValueError, TypeError, OverflowError):
                pass
    return failure


def retry_delay(failure: dict[str, Any], retry: int) -> float | None:
    if failure["code"] not in RETRYABLE_CODES or retry > MAX_RETRIES:
        return None
    provider_delay = failure.get("retry_after_ms")
    if provider_delay is not None:
        return provider_delay / 1000 if provider_delay <= 10000 else None
    return min(min(0.5 * 2 ** (retry - 1), 10) * (0.9 + 0.2 * random.random()), 10)
