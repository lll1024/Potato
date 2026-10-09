"""浏览器验收启动器：正式服务与临时存储，仅替换外部模型／地图。"""
import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile
from contextlib import asynccontextmanager
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from anthropic import AsyncAnthropic
from mcp import Client

from amap_mcp import AmapTools
from test_agent import response
from test_exception_experience import FailingAfterTools, call
from test_itinerary import ItineraryMapService, map_result
from web import Runtime, create_app


async def main():
    model = FailingAfterTools([
        response([call("place", "maps_search_detail", {"id": "海角"}),
                  call("weather", "maps_weather", {"city": "三亚"})], "tool_use"),
        response([call("place-again", "maps_search_detail", {"id": "海角"})], "tool_use"),
        response([{"type": "text", "text": "请确认春节年份；已查询地点，旅行行程仍待安排。"}]),
    ])
    place = {"name": "天涯海角" + "很长的地点名称" * 12,
             "address": "三亚市天涯区" + "很长的地址" * 20, "opentime": "08:00-18:00"}
    maps = ItineraryMapService([map_result(place), map_result({"city": "三亚", "forecasts": [
        {"date": f"2026-10-{day:02d}", "dayweather": "晴", "nightweather": "多云"} for day in range(9, 13)]}),
        map_result(place)])
    @asynccontextmanager
    async def resources():
        tools = AmapTools(cast(Client, maps))
        await tools.discover()
        try:
            yield Runtime(cast(AsyncAnthropic, model), tools, "test-model")
        finally:
            print("COUNTS " + json.dumps({"model": len(model.requests), "map": len(maps.calls)}), flush=True)

    with tempfile.TemporaryDirectory() as directory:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        app = create_app(directory, resources=resources,
            static_dir=Path(__file__).resolve().parents[1] / "frontend" / "dist")
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, timeout_graceful_shutdown=1))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started and not task.done():
            await asyncio.sleep(0)
        print(f"READY http://127.0.0.1:{sock.getsockname()[1]}", flush=True)
        await task
        sock.close()


if __name__ == "__main__":
    asyncio.run(main())
