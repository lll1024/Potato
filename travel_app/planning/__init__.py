"""旅行规划公开入口；契约见 docs/modules/planning.md。"""
from travel_app.agent import Continuation, PlanningResult, main, plan
from travel_app.context import ContextConfigurationError, ContextPolicy


def validate_configuration() -> None:
    """正式启动前检查旅行规划所需的模型容量配置。"""
    ContextPolicy.from_env()

__all__ = ["Continuation", "PlanningResult", "main", "plan", "validate_configuration", "ContextConfigurationError"]
