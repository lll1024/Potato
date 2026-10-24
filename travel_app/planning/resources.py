"""Web 与 CLI 共享的旅行规划运行资源装配。"""
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from anthropic import AsyncAnthropic
from travel_app.retrieval import Retrieval
from travel_app.context import ContextPolicy


@dataclass(frozen=True)
class PlanningResources:
    client: AsyncAnthropic
    tools: Retrieval
    model: str


@asynccontextmanager
async def connected_model(tools: Retrieval, *, client_factory=None):
    base_url = os.getenv("ANTHROPIC_BASE_URL") or None
    async with (client_factory or AsyncAnthropic)(api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        auth_token=None if base_url else os.getenv("ANTHROPIC_AUTH_TOKEN") or None,
        base_url=base_url, timeout=60.0, max_retries=0) as client:
        yield PlanningResources(client, tools, os.environ["MODEL_ID"])


@asynccontextmanager
async def configured_planning(*, retrieval_factory=None):
    from travel_app.retrieval import connected_retrieval
    ContextPolicy.from_env()
    api_key = os.getenv("AMAP_MAPS_API_KEY", "").strip()
    if not api_key or not os.getenv("MODEL_ID"):
        raise RuntimeError("请配置 AMAP_MAPS_API_KEY 和 MODEL_ID。")
    async with (retrieval_factory or connected_retrieval)(api_key, secrets=tuple(os.getenv(name, "") for name in
            ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))) as tools:
        async with connected_model(tools) as resources:
            yield resources
