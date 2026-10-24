"""经正式 Web main，仅注入外部响应地址与合成配置。"""
from contextlib import asynccontextmanager
from functools import partial
import os
import sys

from travel_app.planning import configured_planning
from travel_app.retrieval import connected_retrieval
from travel_app.sessions import Runtime, create_app, main


if __name__ == '__main__':
    directory, port, external = sys.argv[1:]
    os.environ.update(MODEL_ID='test-model', ANTHROPIC_API_KEY='test-key',
                      ANTHROPIC_BASE_URL=external, AMAP_MAPS_API_KEY='test-map-key')
    @asynccontextmanager
    async def resources():
        async with configured_planning(retrieval_factory=partial(connected_retrieval,
                map_url=external + '/maps/mcp', source_url=external + '/maps/mcp')) as ready:
            yield Runtime(ready.client, ready.tools, ready.model)
    sys.argv = ['web.py', '--data-dir', directory, '--port', port]
    raise SystemExit(main(app_factory=partial(create_app, resources=resources),
                          load_environment=lambda *_a, **_k: None))
