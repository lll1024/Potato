"""模型使用独立视图；容量来自部署配置，不从模型名称猜测。"""
import copy
from dataclasses import dataclass, field
from importlib import import_module
import json
import math
import os
import re
from typing import Any, cast


class ContextConfigurationError(ValueError):
    """可向旅行者直接展示的容量配置诊断。"""


@dataclass(frozen=True)
class ContextPolicy:
    window: int
    output_tokens: int = 8000

    @property
    def input_budget(self) -> int:
        return self.window - self.output_tokens - max(2048, (self.window + 9) // 10)

    @property
    def pressure_threshold(self) -> int:
        return self.input_budget * 4 // 5

    @classmethod
    def from_env(cls):
        enabled = os.getenv("CONTEXT_COMPACTION_ENABLED", "false").strip().lower()
        if enabled in ("false", "0", "no", "off", ""):
            return None
        if enabled not in ("true", "1", "yes", "on"):
            raise ContextConfigurationError("请将 CONTEXT_COMPACTION_ENABLED 配置为 true 或 false。")
        raw = os.getenv("MODEL_CONTEXT_WINDOW", "").strip()
        try:
            window = int(raw) if raw.isascii() and raw.isdecimal() else 0
        except ValueError:
            window = 0
        if window <= 0:
            raise ContextConfigurationError("启用上下文压缩须配置 MODEL_CONTEXT_WINDOW 为服务实际支持的正整数 token 容量。")
        policy = cls(window)
        if policy.input_budget <= 0:
            raise ContextConfigurationError("MODEL_CONTEXT_WINDOW 扣除 8,000 tokens 回答额度与安全余量后没有正输入预算，请核对服务实际容量。")
        return policy


@dataclass
class ContextView:
    """record_ids 是会话内原始协议消息位置，不是请求快照或 SQLite payload_id。"""

    messages: list[dict[str, Any]]
    original_watermark: int
    version: int = 0
    record_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_history(cls, messages):
        return cls(copy.deepcopy(messages), len(messages), record_ids=[f"message:{index}" for index in range(1, len(messages) + 1)])

    def append(self, message, original_watermark):
        self.messages.append(copy.deepcopy(message))
        self.original_watermark = original_watermark
        self.record_ids.append(f"message:{original_watermark}")

    def organize_tool_results(self) -> int:
        """仅删除可证明相同的模型副本，不修改原始协议和工具载荷。"""
        changed = 0
        for message in self.messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                raw = block.get("content")
                if not isinstance(raw, str):
                    continue
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(payload, dict) or not isinstance(payload.get("text"), list):
                    continue
                texts = []
                sources = payload.get("sources") if payload.get("provider") == "exa" else None
                for text in payload["text"]:
                    try:
                        duplicate = payload.get("data") is not None and json.dumps(
                            json.loads(text), sort_keys=True, ensure_ascii=False) == json.dumps(
                                payload["data"], sort_keys=True, ensure_ascii=False)
                    except (ValueError, TypeError):
                        duplicate = False
                    if duplicate:
                        continue
                    if isinstance(text, str) and isinstance(sources, list):
                        # 正文完整保留在结构字段；头部、未知元数据及失败警示继续留在 text。
                        for index, source in enumerate(sources):
                            body = source.get("content") if isinstance(source, dict) else None
                            reference = f"[完整内容见 sources[{index}].content]"
                            if isinstance(body, str) and len(body) > len(reference):
                                text = text.replace(body, reference)
                    if text not in texts:
                        texts.append(text)
                if texts == payload["text"]:
                    continue
                if texts:
                    payload["text"] = texts
                else:
                    del payload["text"]
                block["content"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                changed += 1
        return changed


class TokenMeter:
    """计量整个逻辑请求，保留结构开销；实际用量仅向上校准。"""

    def __init__(self, model: str, *, usage_semantics: str = "compatible_unknown"):
        self.encoding = None
        try:
            self.encoding = import_module("tiktoken").encoding_for_model(model)
        except Exception:
            # 可选 tokenizer 或其本地词表不可用时，仍能保守计量。
            pass
        self.factor = 1.0
        self.usage_semantics = usage_semantics

    def measure(self, parameters):
        serialized = json.dumps(parameters, ensure_ascii=False, separators=(",", ":"))
        if self.encoding is not None:
            count = len(self.encoding.encode(serialized, disallowed_special=()))
            method = f"tokenizer:{self.encoding.name}"
        else:
            # 中文按每字 2 tokens；ASCII 单词每 3 字节向上取整，标点每字符计 1。
            # 其他 Unicode 按 UTF-8 字节数，JSON 的键、包裹和转义也参与计量。
            count = sum(math.ceil(len(part) / 3) if part.isascii() and re.fullmatch(r"[A-Za-z0-9_]+", part)
                        else sum(2 if '\u3400' <= char <= '\u9fff' else len(char.encode("utf-8")) for char in part)
                        for part in re.findall(r"[A-Za-z0-9_]+|[^A-Za-z0-9_]", serialized))
            method = "conservative_unicode"
        # SDK/服务消息包装无法由序列化正文精确还原，另外保留固定和每消息开销。
        raw = count + 256 + 12 * len(parameters.get("messages", []))
        return {"estimated_tokens": math.ceil(raw * self.factor), "raw_estimated_tokens": raw,
                "method": method, "approximate": True, "calibration_factor": self.factor}

    def observe_usage(self, usage, measurement):
        actual = None
        semantics = "unknown"
        if isinstance(usage, dict):
            value = usage.get("input_tokens")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                actual = value
                cache_fields = ("cache_creation_input_tokens", "cache_read_input_tokens")
                cache = [usage.get(name) for name in cache_fields]
                if self.usage_semantics == "anthropic":
                    if all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in cache):
                        actual += sum(cast(list[int], cache))
                        semantics = "anthropic_disjoint_input_and_cache"
                    elif not any(item for item in cache):
                        semantics = "input_tokens_returned_cache_unknown"
                    else:
                        semantics = "input_lower_bound_cache_unknown"
                else:
                    semantics = "input_lower_bound_cache_semantics_unknown" if any(cache) else "input_tokens_returned"
                self.factor = max(self.factor, actual / measurement["raw_estimated_tokens"])
        return {"input_tokens_for_calibration": actual, "input_usage_semantics": semantics,
                "calibration_factor": self.factor}
