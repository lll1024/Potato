"""本轮实际模型请求与按提出顺序预留的工具总账。"""
from travel_app.limits import MAX_MODEL_REQUESTS, MAX_TOOL_CALLS


class TurnBudget:
    def __init__(self, max_tool_calls: int = MAX_TOOL_CALLS):
        self.max_tool_calls = max_tool_calls
        self.tool_calls = 0
        self.request_ordinal = 0

    @property
    def finalizing(self) -> bool:
        return not self.remaining_requests() or self.tool_calls >= self.max_tool_calls

    def remaining_requests(self) -> int:
        return max(0, MAX_MODEL_REQUESTS - 1 - self.request_ordinal)

    def next_request_ordinal(self, *, final=False) -> int:
        limit = MAX_MODEL_REQUESTS if final else MAX_MODEL_REQUESTS - 1
        if self.request_ordinal >= limit:
            raise RuntimeError("本轮模型请求额度已耗尽。")
        self.request_ordinal += 1
        return self.request_ordinal

    def reserve_tool(self) -> bool:
        if self.tool_calls >= self.max_tool_calls or not self.remaining_requests():
            return False
        self.tool_calls += 1
        return True

    def release_tool(self) -> None:
        self.tool_calls -= 1
