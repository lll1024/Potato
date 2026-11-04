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
from anthropic import AsyncAnthropic
from mcp import Client
from travel_app.retrieval import Retrieval
from travel_app.sessions import Runtime, create_app
from service_fixtures import MapService, ModelService, response

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


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="potato-ios-") as directory:
        servers = []
        tasks = []
        for name, port in (("populated", 18489), ("empty", 18490)):
            server = uvicorn.Server(uvicorn.Config(
                create_app(Path(directory) / name, resources=resources),
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
            print("READY https://localhost:18489 ; EMPTY https://localhost:18490", flush=True)
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
