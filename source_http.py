"""资料 HTTP 故障只回答对应请求，不取消地图所在的资源作用域。"""
import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx2
from mcp.types import CONNECTION_CLOSED, INTERNAL_ERROR, PARSE_ERROR, REQUEST_TIMEOUT, JSONRPCError, JSONRPCResponse, jsonrpc_message_adapter


class SourceHTTPClient(httpx2.AsyncClient):
    async def send(self, request: httpx2.Request, **kwargs: Any) -> httpx2.Response:
        if request.method == "GET":
            # 搜索／提取只消费 POST 的返回，不开启 SDK 的后台订阅和自动重连。
            return httpx2.Response(405, request=request)
        if request.method != "POST":
            return await super().send(request, **kwargs)
        response = None
        try:
            response = await super().send(request, **kwargs)
            status = response.status_code
            if status >= 400:
                await response.aclose()
                return self.rpc_error(request, INTERNAL_ERROR, status=status)
            if response.headers.get("content-type", "").lower().startswith("text/event-stream"):
                response.stream = _SourceStream(cast(httpx2.AsyncByteStream, response.stream), request, self)
            else:
                await response.aread()
                if response.headers.get("content-type", "").lower().startswith("application/json"):
                    try:
                        reply = jsonrpc_message_adapter.validate_json(response.content, by_name=False)
                        if not isinstance(reply, (JSONRPCResponse, JSONRPCError)) or reply.id != json.loads(request.content).get("id"):
                            raise ValueError("返回无法对应原请求")
                    except ValueError:
                        await response.aclose()
                        return self.rpc_error(request, PARSE_ERROR)
            return response
        except httpx2.TimeoutException:
            if response is not None:
                await response.aclose()
            return self.rpc_error(request, REQUEST_TIMEOUT)
        except (httpx2.TransportError, ConnectionError, OSError):
            if response is not None:
                await response.aclose()
            return self.rpc_error(request, CONNECTION_CLOSED)

    def rpc_error(self, request: httpx2.Request, code: int, *, status: int | None = None) -> httpx2.Response:
        payload = json.loads(request.content)
        error: dict[str, Any] = {"code": code, "message": "资料 HTTP 请求失败"}
        if status is not None:
            error["data"] = {"http_status": status}
        return httpx2.Response(200, request=request, json={
            "jsonrpc": "2.0", "id": payload.get("id"), "error": error,
        })


class _SourceStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream, request: httpx2.Request, client: SourceHTTPClient):
        self.stream, self.request, self.client = stream, request, client

    async def __aiter__(self) -> AsyncIterator[bytes]:
        code = CONNECTION_CLOSED
        try:
            async for chunk in self.stream:
                yield chunk
        except httpx2.TimeoutException:
            code = REQUEST_TIMEOUT
        except (httpx2.TransportError, ConnectionError, OSError):
            pass
        # 完整响应后 SDK 已停止读取；未取得结果的 EOF／断连回填同一 id，避免恢复流重试。
        reply = self.client.rpc_error(self.request, code)
        yield b"\n\nevent: message\ndata: " + reply.content + b"\n\n"

    async def aclose(self) -> None:
        await self.stream.aclose()
