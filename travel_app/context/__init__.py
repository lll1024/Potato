"""上下文记录值适配；当前契约见 docs/modules/context.md。"""
from travel_app.local_records import ContextCheckpoint, PlanningSeed, RecordAbort, TurnMaterial, saved_date_context

__all__ = ["saved_date_context", "ContextCheckpoint", "PlanningSeed", "RecordAbort", "TurnMaterial"]
