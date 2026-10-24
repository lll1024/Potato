"""旅行规划公开入口；契约见 docs/modules/planning.md。"""
from .resources import PlanningResources, configured_planning
from travel_app.retrieval import Retrieval as PlanningTools
from .runtime import Continuation, PlanningResult, SERVICE_MESSAGE, main, plan, run_cli
from travel_app.context import ContextConfigurationError, ContextPolicy


def validate_configuration() -> None:
    """正式启动前检查旅行规划所需的模型容量配置。"""
    ContextPolicy.from_env()

__all__ = ["PlanningResources", "PlanningTools", "configured_planning", "Continuation", "PlanningResult", "main", "plan", "run_cli", "SERVICE_MESSAGE", "validate_configuration", "ContextConfigurationError"]
