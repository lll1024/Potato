"""只经公开 HTTP/SSE 与外部模型边界核验完成通知顺序。"""
import asyncio
import json
import socket
import tempfile
from contextlib import asynccontextmanager

import httpx
import uvicorn
from travel_app.retrieval import Retrieval
from travel_app.sessions import Runtime, create_app
from service_fixtures import MapService
from test_answer_streaming import StreamingModel


async def main():
    model = StreamingModel(query_rounds=0)
    blocked, release_send = asyncio.Event(), asyncio.Event()
    wire = []

    @asynccontextmanager
    async def resources():
        tools = Retrieval(MapService(), api_key="known-secret")
        await tools.discover()
        yield Runtime(model, tools, "test-model")

    with tempfile.TemporaryDirectory(prefix="potato109-sse-order-") as directory:
        app = create_app(directory, resources=resources)

        async def transport(scope, receive, send):
            async def gated_send(message):
                if scope["type"] == "http" and scope.get("path") == "/api/events" and message["type"] == "http.response.body":
                    body = message.get("body", b"").decode()
                    for line in body.splitlines():
                        if line.startswith("data:"):
                            event = json.loads(line[5:])
                            # 标准 ASGI 发送背压，只控制 HTTP 运输边界，不接触生产运行对象。
                            if not blocked.is_set() and event["kind"] == "turn.accepted":
                                blocked.set()
                                await release_send.wait()
                            wire.append(event)
                await send(message)
            await app(scope, receive, gated_send)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(transport, log_level="critical", access_log=False, timeout_graceful_shutdown=1))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            while not server.started and not task.done():
                await asyncio.sleep(0)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}") as client:
                initial = (await client.get("/api/snapshot")).json()
                accepted = (await client.post("/api/turns", json={"input": "仅据现有信息给杭州简短安排", "submission_id": "sse-order-audit"})).json()
                await asyncio.wait_for(model.entered.wait(), 3)
                model.release.put_nowait(None)
                await asyncio.wait_for(model.delivered.get(), 3)
                readable = (await client.get("/api/snapshot")).json()
                assert readable["answer_preview"]["text"] == model.parts[0]
                assert readable["active_turn_id"] == accepted["turn_id"]
                print("BEFORE_SSE " + json.dumps({"cursor": readable["cursor"], "active": True, "answer_text": readable["answer_preview"]["text"], "answer_revision": readable["answer_revision"]}, ensure_ascii=False))

                received = []
                async def consume():
                    async with client.stream("GET", f'/api/events?after={initial["cursor"]}&stream_id={initial["stream_id"]}') as stream:
                        assert stream.status_code == 200
                        async for line in stream.aiter_lines():
                            if line.startswith("data:"):
                                event = json.loads(line[5:])
                                received.append(event)
                                if event["kind"] == "turn.finished":
                                    return
                reader = asyncio.create_task(consume())
                await asyncio.wait_for(blocked.wait(), 3)
                for _ in range(len(model.parts)):
                    model.release.put_nowait(None)
                async with asyncio.timeout(3):
                    while True:
                        page = (await client.get("/api/sessions/" + accepted["session_id"])).json()
                        if page["turns"][0]["status"] != "running":
                            break
                        await asyncio.sleep(0)
                final_snapshot = (await client.get("/api/snapshot")).json()
                assert page["turns"][0]["status"] == "completed"
                assert page["turns"][0]["answer"] == "".join(model.parts)
                assert final_snapshot["active_turn_id"] is None
                assert final_snapshot["answer_preview"] is None
                print("WHILE_SEND_BLOCKED " + json.dumps({"history_status": page["turns"][0]["status"], "history_answer_chars": len(page["turns"][0]["answer"]), "cursor": final_snapshot["cursor"], "model_requests": len(model.requests)}, ensure_ascii=False))
                release_send.set()
                await asyncio.wait_for(reader, 3)
                for index, event in enumerate(received):
                    item = {"index": index, "kind": event["kind"]}
                    if "cursor" in event:
                        item["cursor"] = event["cursor"]
                    if event["kind"] == "service.state":
                        item["active_turn_id"] = event["state"]["active_turn_id"]
                        item["answer_revision"] = event["state"]["answer_revision"]
                    if event["kind"] in ("answer.preview", "progress.preview"):
                        item["preview"] = event["preview"]
                        item["revision"] = event["revision"]
                    print("SSE " + json.dumps(item, ensure_ascii=False))
                finish = next(i for i, event in enumerate(received) if event["kind"] == "turn.finished")
                null = next(i for i, event in enumerate(received) if event["kind"] == "answer.preview" and event["preview"] is None)
                inactive = next(i for i, event in enumerate(received) if event["kind"] == "service.state" and event["state"]["active_turn_id"] is None)
                assert inactive < null < finish
                print(f"RESULT REACHABLE inactive_index={inactive} answer_null_index={null} turn_finished_index={finish}")
        finally:
            release_send.set()
            for _ in range(8):
                model.release.put_nowait(None)
            server.should_exit = True
            await task
            sock.close()


asyncio.run(main())
