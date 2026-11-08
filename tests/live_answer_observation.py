"""实连验收的只读外部边界观测；不记录原始文本、工具参数或推理。"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import time

__all__ = ["Recorder", "ObservedClient", "ObservedSSE"]


class Recorder:
    def __init__(self):
        self.events = []
        self.key = secrets.token_bytes(32)
        self.redact = lambda value: value
        self.sequence = 0

    def record(self, layer, kind, **fields):
        self.events.append({"at": datetime.now(timezone.utc).isoformat(),
                            "epoch_ms": time.time() * 1000, "layer": layer, "kind": kind, **fields})

    def fingerprint(self, parameters):
        parameters = {key: value for key, value in parameters.items() if key != "stream"}
        safe = json.loads(self.redact(json.dumps(parameters, ensure_ascii=False)))
        encoded = json.dumps(safe, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        return hmac.new(self.key, encoded, hashlib.sha256).hexdigest()

    def start(self, parameters, delivery):
        self.sequence += 1
        self.record("sdk", "request", call_sequence=self.sequence, delivery=delivery,
                    fingerprint=self.fingerprint(parameters))
        return self.sequence

    def returned(self, response, sequence):
        self.record("sdk", "returned", call_sequence=sequence,
                    text_chars=sum(len(item.text) for item in response.content if item.type == "text"),
                    stop_reason=response.stop_reason)


class ObservedClient:
    def __init__(self, upstream, recorder):
        self.messages = ObservedMessages(upstream.messages, recorder)


class ObservedMessages:
    def __init__(self, upstream, recorder):
        self.upstream = upstream
        self.recorder = recorder

    async def create(self, **parameters):
        sequence = self.recorder.start(parameters, "complete")
        try:
            response = await self.upstream.create(**parameters)
            self.recorder.returned(response, sequence)
            return response
        except Exception as error:
            self.recorder.record("sdk", "error", call_sequence=sequence, error_type=type(error).__name__)
            raise

    @asynccontextmanager
    async def stream(self, **parameters):
        recorder = self.recorder
        sequence = recorder.start(parameters, "stream")
        try:
            async with self.upstream.stream(**parameters) as upstream:
                class Stream:
                    async def __aiter__(self):
                        chars = 0
                        async for item in upstream:
                            text = ""
                            if item.type == "content_block_delta" and item.delta.type == "text_delta":
                                text = item.delta.text
                            elif item.type == "content_block_start" and item.content_block.type == "text":
                                text = item.content_block.text
                            if text:
                                chars += len(text)
                                recorder.record("sdk", "text", call_sequence=sequence, index=item.index,
                                                text_chars=chars, delta_chars=len(text))
                            yield item

                    async def get_final_message(self):
                        response = await upstream.get_final_message()
                        recorder.returned(response, sequence)
                        return response
                yield Stream()
        except Exception as error:
            recorder.record("sdk", "error", call_sequence=sequence, error_type=type(error).__name__)
            raise


def content_metadata(value):
    if not isinstance(value, dict):
        return None
    result = {key: value[key] for key in ("instance_id", "stream_id", "session_id", "turn_id", "attempt_id",
              "revision", "status", "delivery", "active_turn_id", "active_session_id") if key in value}
    if "text" in value:
        result["text_chars"] = len(value["text"])
    if "entries" in value:
        result["entries"] = [{**{key: item[key] for key in ("id", "attempt_id", "kind", "status", "delivery") if key in item},
                              "text_chars": len(item.get("text", ""))} for item in value["entries"]]
    return result


class ObservedSSE:
    def __init__(self, app, recorder):
        self.app = app
        self.recorder = recorder
        self.connections = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/events":
            return await self.app(scope, receive, send)
        self.connections += 1
        connection = self.connections
        headers = dict(scope.get("headers", []))
        agent = headers.get(b"user-agent", b"").decode(errors="replace")
        client = "web" if "Chrome" in agent else "ios" if "Potato" in agent else "other"
        pending = b""

        async def observed_send(message):
            nonlocal pending
            if message["type"] == "http.response.body":
                pending += message.get("body", b"").replace(b"\r\n", b"\n")
                while b"\n\n" in pending:
                    frame, pending = pending.split(b"\n\n", 1)
                    lines = frame.decode().splitlines()
                    kind = next((line[7:] for line in lines if line.startswith("event: ")), "message")
                    data = "\n".join(line[6:] for line in lines if line.startswith("data: "))
                    if data:
                        value = json.loads(data)
                        if kind in ("answer.preview", "progress.preview", "service.state"):
                            self.recorder.record("backend_sse_send", kind, connection_id=connection,
                                                 client=client, revision=value.get("revision"),
                                                 content=content_metadata(value.get("state") if kind == "service.state" else value.get("preview")))
                        elif kind == "trace":
                            self.recorder.record("backend_sse_send", kind, connection_id=connection, client=client,
                                content={key: value[key] for key in ("kind", "cursor", "session_id", "turn_id", "request_id", "created_at") if key in value})
            await send(message)
        await self.app(scope, receive, observed_send)
