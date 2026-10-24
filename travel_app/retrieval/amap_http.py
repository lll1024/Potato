"""在 SDK 丢失 HTTP 故障信息前，转换为原请求对应的协议错误。"""

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx2
from mcp.types import CONNECTION_CLOSED, INTERNAL_ERROR, REQUEST_TIMEOUT


class AmapHTTPClient(httpx2.AsyncClient):
    connection_failed: bool = False

    async def send(self, request: httpx2.Request, **kwargs: Any) -> httpx2.Response:
        if request.method != "POST":
            return await super().send(request, **kwargs)
        response = None
        try:
            response = await super().send(request, **kwargs)
            status = response.status_code
            if status in (401, 403, 429) or status >= 500:
                await response.aclose()
                return self._rpc_error(request, INTERNAL_ERROR, status=status)
            if status in (404, 410):
                await response.aclose()
                return self._rpc_error(request, CONNECTION_CLOSED)
            if response.headers.get("content-type", "").lower().startswith("text/event-stream"):
                response.stream = _MCPStream(cast(httpx2.AsyncByteStream, response.stream), request, self)
            else:
                await response.aread()
            return response
        except httpx2.TimeoutException:
            if response is not None:
                await response.aclose()
            return self._rpc_error(request, REQUEST_TIMEOUT)
        except (httpx2.TransportError, ConnectionError, OSError):
            self.connection_failed = True
            if response is not None:
                await response.aclose()
            return self._rpc_error(request, CONNECTION_CLOSED)

    def _rpc_error(
        self, request: httpx2.Request, code: int, *, status: int | None = None,
    ) -> httpx2.Response:
        payload = json.loads(request.content)
        error: dict[str, Any] = {"code": code, "message": "地图 HTTP 请求失败"}
        if status is not None:
            error["data"] = {"http_status": status}
        return httpx2.Response(200, request=request, json={
            "jsonrpc": "2.0", "id": payload.get("id"), "error": error,
        })


class _MCPStream(httpx2.AsyncByteStream):
    """SSE 中途断开时回答原请求，避免 SDK 自动恢复流而丢失故障类型。"""

    def __init__(self, stream: httpx2.AsyncByteStream, request: httpx2.Request, client: AmapHTTPClient):
        self.stream = stream
        self.request = request
        self.client = client

    async def __aiter__(self) -> AsyncIterator[bytes]:
        code = CONNECTION_CLOSED
        try:
            async for chunk in self.stream:
                yield chunk
        except httpx2.TimeoutException:
            code = REQUEST_TIMEOUT
        except (httpx2.TransportError, ConnectionError, OSError):
            pass
        # 正常返回结果后 SDK 会停止读取；读到 EOF 表示该请求尚无结果。
        if code == CONNECTION_CLOSED:
            self.client.connection_failed = True
        reply = self.client._rpc_error(self.request, code)
        yield b"\n\nevent: message\ndata: " + reply.content + b"\n\n"

    async def aclose(self) -> None:
        await self.stream.aclose()
