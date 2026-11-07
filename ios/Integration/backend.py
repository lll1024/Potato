"""iOS 验收服务：真实 HTTP/SQLite，只替换外部模型与查询资源。"""
import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import sys
import tempfile
from typing import cast
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import httpx
import uvicorn
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse
from anthropic import AsyncAnthropic
from mcp import Client
from travel_app.retrieval import Retrieval
from travel_app.sessions import Runtime, create_app
from service_fixtures import MapService, ModelService, response
from preference_fixtures import Clock, PreferenceModel
from model_fixtures import status_error
from record_faults import fail_event

INPUT = "请安排杭州两日旅行。" + "保留完整的当前旅行要求。" * 20 + "输入全文结尾"
ANSWER = "# 杭州旅行\n\n" + "西湖步行，午后休息，次日参观博物馆。\n\n" * 20 + "回答全文结尾"
READING_ANSWER = Path(__file__).with_name("reading-answer.md").read_text()


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


class ReadingModel(SubmissionModel):
    async def create(self, **kwargs):
        if kwargs["system"].startswith("旅行者偏好提取"):
            return await super().create(**kwargs)
        return response([{"type": "text", "text": READING_ANSWER}])


class StreamingModel:
    """分块和最终返回由 HTTP 屏障控制；会话、规划与 SQLite 仍使用真实实现。"""
    parts = ["## 实时杭州旅行\n\n", "上午游览西湖。\n\n", "下午参观博物馆。\n"]

    def __init__(self):
        self.messages = self
        self.reset()

    def reset(self, mode="normal"):
        self.release = asyncio.Queue()
        self.delivered = asyncio.Queue()
        self.entered = asyncio.Event()
        self.returned = False
        self.mode = mode
        self.parts = type(self).parts
        if mode == "reading":
            self.parts = [
                "## 流式中文阅读\n\n" + "沿湖慢行，按体力安排休息。\n\n" * 16 + "```swift\nlet city = \"杭州\"",
                "\n```\n\n| 日期 | 上午 | 下午 | 晚间 | 提醒 |\n",
                "| --- | --- | --- | --- | --- |\n| 周六 | 西湖漫步 | 博物馆 | 湖畔晚餐 | 提前预约 |\n\n[预约说明](https://example.com",
                ")\n\n" + "继续阅读中文旅行安排，保留已经读到的位置。\n\n" * 12 + "第四批阅读标记。\n\n",
                "- 步行路线按体力调整。\n- 午后留出休息时间。\n\n" + "最新一批旅行建议。\n\n" * 12,
            ]
        self.attempts = 0
        self.query_done = False

    async def create(self, **parameters):
        if self.query_done and self.mode == "fallback":
            await self.release.get()
            self.returned = True
            self.delivered.put_nowait("returned")
            return response([{"type": "text", "text": "完整的杭州安排。"}])
        self.query_done = True
        return response([{"type": "text", "text": "查询阶段说明，不是最终回答。"}])

    @asynccontextmanager
    async def stream(self, **parameters):
        model = self
        self.attempts += 1
        fail = self.mode in {"retry", "fallback"} and self.attempts == 1

        class Stream:
            async def __aiter__(self):
                model.entered.set()
                yield SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="text", text=""))
                if fail:
                    await model.release.get()
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="失败旧正文"))
                    model.delivered.put_nowait("partial")
                    await model.release.get()
                    model.delivered.put_nowait("failed")
                    if model.mode == "fallback":
                        raise status_error(400, {"error": {"code": "streaming_not_supported"}})
                    raise status_error(429, headers={"Retry-After": "3"})
                for part in model.parts:
                    await model.release.get()
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=part))
                    model.delivered.put_nowait(part)
                await model.release.get()

            async def get_final_message(self):
                model.returned = True
                model.delivered.put_nowait("returned")
                return response([{"type": "text", "text": "".join(model.parts)}])

        yield Stream()


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
        streaming_model = StreamingModel()
        unsaved_model = StreamingModel()

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

        @asynccontextmanager
        async def reading_resources():
            tools = Retrieval(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, ReadingModel()), tools, "test-model")

        @asynccontextmanager
        async def streaming_resources():
            tools = Retrieval(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, streaming_model), tools, "test-model")

        @asynccontextmanager
        async def unsaved_resources():
            tools = Retrieval(cast(Client, MapService()))
            await tools.discover()
            yield Runtime(cast(AsyncAnthropic, unsaved_model), tools, "test-model")

        for name, port in (("populated", 18489), ("empty", 18490), ("submissions", 18492), ("recovery", 18493), ("lifecycle", 18494), ("reading", 18495), ("local-http", 18496), ("streaming", 18497), ("unsaved", 18498)):
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

            if name == "reading":
                app = create_app(Path(directory) / name, resources=reading_resources,
                                 static_dir=Path(directory) / "no-static")

                @app.get("/__test__/booking", response_class=HTMLResponse)
                async def booking():
                    return '<html lang="zh"><meta charset="utf-8"><title>预约入口样例</title><h1>预约入口样例</h1><p>尚未预约。这是客户端链接验收页面。</p></html>'

            if name == "streaming":
                app = create_app(Path(directory) / name, resources=streaming_resources,
                                 static_dir=Path(directory) / "no-static")

                @app.post("/__test__/arm")
                async def arm_stream(mode: str = "normal"):
                    streaming_model.reset(mode)
                    return {"armed": True}

                @app.get("/__test__/entered")
                async def entered_stream():
                    async with asyncio.timeout(10):
                        await streaming_model.entered.wait()
                    return {"entered": True}

                @app.post("/__test__/step")
                async def step_stream():
                    streaming_model.release.put_nowait(None)
                    async with asyncio.timeout(10):
                        await streaming_model.delivered.get()
                    return {"returned": streaming_model.returned}

            if name == "unsaved":
                app = create_app(Path(directory) / "unsaved", resources=unsaved_resources,
                                 static_dir=Path(directory) / "no-static")

                @app.post("/__test__/arm")
                async def arm_unsaved():
                    unsaved_model.reset()
                    return {"armed": True}

                @app.get("/__test__/entered")
                async def entered_unsaved():
                    async with asyncio.timeout(10):
                        await unsaved_model.entered.wait()
                    # 只在真实 SQLite 完成事务注入故障，正文与停止仍走生产实现。
                    fail_event(Path(directory) / "unsaved", "turn.finished")
                    return {"entered": True}

                @app.post("/__test__/step")
                async def step_unsaved():
                    unsaved_model.release.put_nowait(None)
                    async with asyncio.timeout(10):
                        await unsaved_model.delivered.get()
                    return {"returned": unsaved_model.returned}

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
                ssl_certfile=None if name == "local-http" else args.cert,
                ssl_keyfile=None if name == "local-http" else args.key))
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
            async with httpx.AsyncClient(base_url="https://localhost:18495", verify=args.cert) as client:
                async def complete(input_text, session_id=None):
                    accepted = await client.post("/api/turns", json={"input": input_text, "session_id": session_id})
                    accepted.raise_for_status()
                    async with asyncio.timeout(10):
                        while (await client.get("/api/snapshot")).json()["active_turn_id"]:
                            await asyncio.sleep(.01)
                    return accepted.json()["session_id"]

                # 分页始终通过真实 HTTP 接受与保存，不伪造列表或历史响应。
                for index in range(52):
                    await complete(f"阅读样例会话 {index + 1:02d}")
                session_id = await complete("杭州两日旅行阅读验收第1轮：保留完整输入。")
                for index in range(2, 56):
                    await complete(f"杭州两日旅行阅读验收第{index}轮：保留完整输入。", session_id)
            print("READY https://localhost:18489 ; EMPTY https://localhost:18490 ; SUBMISSIONS https://localhost:18492 ; RECOVERY https://localhost:18493 ; LIFECYCLE https://localhost:18494 ; READING https://localhost:18495 ; LOCAL HTTP http://127.0.0.1:18496 ; STREAMING https://localhost:18497", flush=True)
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
