"""不引用规划、上下文或偏好运行对象的持久化值。"""
from dataclasses import dataclass
import json


class RecordAbort(RuntimeError):
    """事实未可靠提交；停止后续调用，等待在途调用及保存尝试收束。"""


@dataclass(frozen=True)
class ContextCheckpoint:
    version: int
    original_watermark: int
    state_json: str

    @classmethod
    def capture(cls, state: dict) -> "ContextCheckpoint":
        return cls(state["version"], state["original_watermark"],
                   json.dumps(state, ensure_ascii=False))
