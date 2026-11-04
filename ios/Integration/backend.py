"""iOS 验收服务：真实 HTTP/SQLite，只替换外部模型与查询资源。"""
import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import sys
import tempfile
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import httpx
import uvicorn
from fastapi.responses import JSONResponse
from anthropic import AsyncAnthropic
from mcp import Client
from travel_app.retrieval import Retrieval
from travel_app.sessions import Runtime, create_app
from service_fixtures import MapService, ModelService, response
from preference_fixtures import Clock, PreferenceModel

INPUT = "请安排杭州两日旅行。" + "保留完整的当前旅行要求。" * 20 + "输入全文结尾"
ANSWER = "# 杭州旅行\n\n" + "西湖步行，午后休息，次日参观博物馆。\n\n" * 20 + "回答全文结尾"


@contextmanager
def uncaptured_signals():
    yield


@asynccontextmanager
async def resources():
    tools = Retrieval(cast(Client, MapService()))
    await tools.discover()
    model = ModelService([response([{"type": "text", "text": ANSWER}])] * 8)
    yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")


class SubmissionModel(PreferenceModel):
    async def create(self, **kwargs):
        if kwargs["system"].startswith("旅行者偏好提取"):
            return await super().create(**kwargs)
        self.requests.append(kwargs)
        text = "已读取共同偏好：不吃辣。" if "不吃辣" in kwargs["system"] else "尚无饮食偏好。"
        return response([{"type": "text", "text": text + "\n\n" + ANSWER}])


@asynccontextmanager
async def submission_resources():
    tools = Retrieval(cast(Client, MapService()))
    await tools.discover()
    yield Runtime(cast(AsyncAnthropic, SubmissionModel()), tools, "test-model")


class LifecycleModel(SubmissionModel):
    """只挂起实际模型调用；停止、保存及全局名额由真实后端负责。"""
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.hold_next = False

    async def create(self, **kwargs):
        if not kwargs["system"].startswith("旅行者偏好提取") and self.hold_next:
            self.hold_next = False
            self.entered.set()
            await self.release.wait()
            self.requests.append(kwargs)
            return response([{"type": "tool_use", "id": "held-query", "name": "maps_text_search",
                              "input": {"keywords": "西湖", "city": "杭州"}}], "tool_use")
        return await super().create(**kwargs)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="potato-ios-") as directory:
        servers = []
        tasks = []
        clock = Clock()
        recovery_model = SubmissionModel()
        lifecycle_model = LifecycleModel()

        @asynccontextmanager
        async def recovery_resources():
            tools = Retrieval(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, recovery_model), tools, "test-model")

        @asynccontextmanager
        async def lifecycle_resources():
            tools = Retrieval(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, lifecycle_model), tools, "test-model")

        for name, port in (("populated", 18489), ("empty", 18490), ("submissions", 18492), ("recovery", 18493), ("lifecycle", 18494)):
            app = (create_app(Path(directory) / name, resources=submission_resources,
                              preference_clock=clock, preference_wait=clock.wait)
                   if name == "submissions" else create_app(Path(directory) / name, resources=resources))
            if name == "recovery":
                app = create_app(Path(directory) / name, resources=recovery_resources,
                                 static_dir=Path(directory) / "no-static")

                @app.get("/__test__/model-count")
                async def model_count():
                    return {"count": sum(not request["system"].startswith("旅行者偏好提取")
                                         for request in recovery_model.requests)}

            if name == "lifecycle":
                app = create_app(Path(directory) / name, resources=lifecycle_resources,
                                 static_dir=Path(directory) / "no-static")

                @app.post("/__test__/arm")
                async def arm():
                    lifecycle_model.entered.clear()
                    lifecycle_model.release.clear()
                    lifecycle_model.hold_next = True
                    return {"armed": True}

                @app.get("/__test__/entered")
                async def entered():
                    async with asyncio.timeout(10):
                        await lifecycle_model.entered.wait()
                    return {"entered": True}

                @app.post("/__test__/release")
                async def release():
                    lifecycle_model.release.set()
                    return {"released": True}

            if name == "submissions":
                delay_next_history = False

                @app.middleware("http")
                async def delayed_acceptance(request, call_next):
                    nonlocal delay_next_history
                    if request.url.path == "/__test__/delay-history" and request.method == "POST":
                        delay_next_history = True
                        return JSONResponse({"delaying": True})
                    if delay_next_history and request.method == "GET" and request.url.path.startswith("/api/sessions/"):
                        delay_next_history = False
                        await asyncio.sleep(4)
                    submitted_input = ((await request.json()).get("input")
                                       if request.method == "POST" and request.url.path == "/api/turns" else None)
                    long_input = isinstance(submitted_input, str) and submitted_input.startswith("长原文核对")
                    delay = (request.method == "POST" and request.url.path == "/api/turns"
                             and (long_input or submitted_input in ("延迟接受时切换会话", "延迟接受时保留草稿", "重启核对原提交")))
                    result = await call_next(request)
                    if delay and result.status_code == 202:
                        # 仅延迟 HTTP 接受响应；真实轮次与 SQLite 由原后端推进。
                        await asyncio.sleep(20 if long_input else 8 if submitted_input in ("延迟接受时保留草稿", "重启核对原提交") else 4)
                    return result
            server = uvicorn.Server(uvicorn.Config(
                app,
                host="127.0.0.1", port=port, log_level="critical", access_log=False,
                ssl_certfile=args.cert, ssl_keyfile=args.key))
            # 由顶层处理信号，确保两个服务都能退出。
            server.capture_signals = uncaptured_signals
            servers.append(server)
            tasks.append(asyncio.create_task(server.serve()))
        try:
            async with asyncio.timeout(10):
                while not all(server.started for server in servers):
                    await asyncio.sleep(.01)
            async with httpx.AsyncClient(base_url="https://localhost:18489", verify=args.cert) as client:
                accepted = await client.post("/api/turns", json={"input": INPUT})
                accepted.raise_for_status()
                async with asyncio.timeout(10):
                    while (await client.get("/api/snapshot")).json()["active_turn_id"]:
                        await asyncio.sleep(.01)
                page = (await client.get("/api/sessions/" + accepted.json()["session_id"],
                                         params={"include_messages": "false"})).json()
                assert page["turns"][0]["input"] == INPUT
                assert page["turns"][0]["answer"] == ANSWER
            async with httpx.AsyncClient(base_url="https://localhost:18492", verify=args.cert) as client:
                accepted = await client.post("/api/turns", json={"input": "我一直不吃辣"})
                accepted.raise_for_status()
                async with asyncio.timeout(10):
                    while (await client.get("/api/snapshot")).json()["active_turn_id"]:
                        await asyncio.sleep(.01)
                    await clock.advance(3601)
                    while not (await client.get("/api/preferences")).json()["preferences"]:
                        await asyncio.sleep(.01)
                preferences = (await client.get("/api/preferences")).json()["preferences"]
                assert preferences[0]["content"] == "不吃辣"
            print("READY https://localhost:18489 ; EMPTY https://localhost:18490 ; SUBMISSIONS https://localhost:18492 ; RECOVERY https://localhost:18493 ; LIFECYCLE https://localhost:18494", flush=True)
            await asyncio.gather(*tasks)
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
