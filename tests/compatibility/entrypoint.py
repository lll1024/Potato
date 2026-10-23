"""正式根入口的测试装配：只重定向外部服务、配置与时钟。"""
from datetime import datetime
from functools import partial
import os
from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    mode, root, external, *arguments = sys.argv[1:]
    os.environ.update(MODEL_ID="test-model", ANTHROPIC_API_KEY="synthetic-key",
                      ANTHROPIC_BASE_URL=external, AMAP_MAPS_API_KEY="synthetic-map-key",
                      CONTEXT_COMPACTION_ENABLED="true", MODEL_CONTEXT_WINDOW="40000")
    # 不读取项目或个人 .env；模型与 MCP 均走生产网络 adapter。
    from travel_app import agent, web, travel_tools
    travel_tools.EXA_URL = external + "/maps/mcp"
    target = web if mode == "web" else agent
    target.load_dotenv = lambda *_args, **_kwargs: None
    original_transport = target.streamable_http_client
    target.streamable_http_client = lambda _url, **kwargs: original_transport(external + "/maps/mcp", **kwargs)
    clock = lambda: datetime.fromisoformat("2026-10-23T09:00:00+08:00")
    if mode == "web":
        directory, port, clock_file = arguments
        import asyncio
        async def wait(_delay):
            await asyncio.sleep(.02)
        web.create_app = partial(web.create_app, clock=clock,
                                 preference_clock=lambda: float(Path(clock_file).read_text()),
                                 preference_wait=wait)
        sys.argv = ["web.py", "--data-dir", directory, "--port", port]
    else:
        agent.run_cli = partial(agent.run_cli, clock=clock)
        sys.argv = ["agent.py", *arguments]
    runpy.run_path(str(Path(root) / (mode == "web" and "web.py" or "agent.py")), run_name="__main__")
