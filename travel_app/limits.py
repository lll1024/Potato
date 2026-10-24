"""单次用户请求的初始预算；后续入口复用同一组默认值。"""

TOOL_TIMEOUT = 30.0
# 个人开发者相关服务为 3 QPS；统一间隔留出计时余量。
MAP_CALL_INTERVAL = 0.4
# 资料侧独立的本机请求间隔，不代表供应商账户额度或官方 QPS。
SOURCE_CALL_INTERVAL = 0.4
# 逻辑步骤防护与实际请求额度独立；重试、恢复和摘要均按实际请求计数。
MAX_ROUNDS = 80
MAX_TOOL_CALLS = 128
MAX_MODEL_REQUESTS = 64


__all__ = ["TOOL_TIMEOUT", "MAP_CALL_INTERVAL", "SOURCE_CALL_INTERVAL", "MAX_ROUNDS", "MAX_TOOL_CALLS", "MAX_MODEL_REQUESTS"]
