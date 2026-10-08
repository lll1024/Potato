"""通过正式入口运行，仅将外部地图 SDK 连接路由到本机替身。"""
import os
import sys

import web


if __name__ == '__main__':
    directory, port, external = sys.argv[1:]
    os.environ.update(MODEL_ID='test-model', ANTHROPIC_API_KEY='test-key',
                      ANTHROPIC_BASE_URL=external, AMAP_MAPS_API_KEY='test-map-key')
    # 自动测试只用合成配置，主工作区即使有真实 .env 也不读取。
    web.load_dotenv = lambda *_args, **_kwargs: None
    original = web.streamable_http_client
    web.streamable_http_client = lambda _url, **kwargs: original(external + '/maps/mcp', **kwargs)
    sys.argv = ['web.py', '--data-dir', directory, '--port', port]
    raise SystemExit(web.main())
