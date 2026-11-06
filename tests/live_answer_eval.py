"""显式启动真实模型验收：生产装配、隔离 SQLite，不替换或延迟模型输出。"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

import uvicorn
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from travel_app.sessions import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=True)
    with tempfile.TemporaryDirectory(prefix="potato-live-answer-") as directory:
        app = create_app(directory, static_dir=Path(directory) / "no-static")

        @app.get("/__acceptance__/environment")
        async def environment():
            return {"kind": "configured-model-answer-acceptance",
                    "model": os.environ["MODEL_ID"],
                    "provider": urlsplit(os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").hostname}

        app.mount("/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="page")
        uvicorn.run(app, host="127.0.0.1", port=18499, ssl_certfile=args.cert,
                    ssl_keyfile=args.key, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
