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
    import travel_app.planning as planning
    import travel_app.sessions as sessions
    from travel_app.retrieval import connected_retrieval
    factory = partial(connected_retrieval, map_url=external + "/maps/mcp", source_url=external + "/maps/mcp")
    clock = lambda: datetime.fromisoformat("2026-10-23T09:00:00+08:00")
    if mode == "web":
        directory, port, clock_file = arguments
        import asyncio
        async def wait(_delay):
            await asyncio.sleep(.02)
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def resources():
            async with planning.configured_planning(retrieval_factory=factory) as ready:
                yield sessions.Runtime(ready.client, ready.tools, ready.model)
        app_factory = partial(sessions.create_app, resources=resources, clock=clock,
            preference_clock=lambda: float(Path(clock_file).read_text()), preference_wait=wait)
        sessions.main = partial(sessions.main, app_factory=app_factory, load_environment=lambda *_a, **_k: None)
        sys.argv = ["web.py", "--data-dir", directory, "--port", port]
    else:
        planning.main = partial(planning.main, load_environment=lambda *_a, **_k: None,
            cli_runner=partial(planning.run_cli, clock=clock, retrieval_factory=factory))
        sys.argv = ["agent.py", *arguments]
    runpy.run_path(str(Path(root) / (mode == "web" and "web.py" or "agent.py")), run_name="__main__")
