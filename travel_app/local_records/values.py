"""不引用规划、上下文或偏好运行对象的持久化值。"""
from dataclasses import dataclass
import json


class RecordAbort(RuntimeError):
    """事实未可靠提交；停止后续调用，等待在途调用及保存尝试收束。"""
    def __init__(self, message: str = "", *, kind: str | None = None):
        self.kind = kind
        super().__init__(message)


@dataclass(frozen=True)
class ContextCheckpoint:
    version: int
    original_watermark: int
    state_json: str

    @classmethod
    def capture(cls, state: dict) -> "ContextCheckpoint":
        return cls(state["version"], state["original_watermark"],
                   json.dumps(state, ensure_ascii=False))


@dataclass(frozen=True)
class PlanningSeed:
    """同一读取快照中的完整协议、有效上下文及历史资料索引。"""
    messages_json: str = "[]"
    context_json: str | None = None
    date_context_json: str | None = None
    source_materials_json: str = "[]"


@dataclass(frozen=True)
class TurnMaterial:
    """轮次共同保存材料；JSON 在规划返回时冻结。"""
    messages_json: str
    context_checkpoint: ContextCheckpoint | None = None


@dataclass(frozen=True)
class PreferenceState:
    """偏好记录及资格事实的一致快照；不携带连接或业务规则。"""
    state_json: str

    @classmethod
    def capture(cls, state: dict) -> "PreferenceState":
        return cls(json.dumps(state, ensure_ascii=False, sort_keys=True))

    def data(self) -> dict:
        return json.loads(self.state_json)
