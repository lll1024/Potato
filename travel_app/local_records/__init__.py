"""本机记录的值与中止契约；当前迁移状态见 docs/modules/local-records.md。"""
from .values import ContextCheckpoint, RecordAbort

__all__ = ["ContextCheckpoint", "RecordAbort"]
