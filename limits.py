"""单次用户请求的初始预算；后续入口复用同一组默认值。"""

TOOL_TIMEOUT = 30.0
# 个人开发者相关服务为 3 QPS；统一间隔留出计时余量。
MAP_CALL_INTERVAL = 0.4
# 资料侧独立的本机请求间隔，不代表供应商账户额度或官方 QPS。
SOURCE_CALL_INTERVAL = 0.4
MAX_ROUNDS = 20
MAX_TOOL_CALLS = 64
