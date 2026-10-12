---
status: accepted
---

# 模型请求在当前步骤有限重试，失败后以新轮次继续

旅行助手可能在已获得多项查询资料后因模型连接失败结束对话轮次。采用 deepseek-harness 的默认 `normal` 策略，在当前模型步骤内有限重试；相比立即结束或无限等待，这能自动处理瞬时故障，并为持续失败保留明确终态。重试期间沿用本轮用户输入、时间上下文、旅行者偏好快照和已完成工具结果，成功响应才推进对话；不通过重发用户需求来执行自动重试，也不重新执行之前完成的工具调用。

每个模型步骤在首次尝试后最多追加 5 次重试，下一步骤重新计数。只重试空响应、限流、服务端错误、超时及连接故障，认证、配额、上下文超限、无效请求及未知错误直接结束。初始等待 500 毫秒，指数退避，上限 10 秒，抖动 ±10%；有效且不超过 10 秒的 `Retry-After` 优先使用原值，超过上限则结束本轮，缺失或无效则采用本地退避。由应用统一管理重试，SDK 保持 `max_retries=0`；既有单次请求超时保持不变，不额外增加整轮时间上限。

停止操作优先于重试，等待可中断，停止后不得发起新尝试；已在执行的请求沿用现有停止与真实结果记录契约。每次实际请求尝试独立保留身份、开始与结束、响应或错误、实际用量；安排重试与退避结束后的重试恢复分别持久记录并在界面展示，实际请求是否开始以该请求的开始记录为准，等待被停止或服务中断时不能显示为已经发出请求。应用提示、错误详情和失败响应不得混入自动重试的模型输入。重试不消耗额外逻辑步骤或工具调用预算，但每次实际请求仍计入执行轨迹和用量；缺失用量保持未知。

重试耗尽后本轮明确结束为失败，保留已获得的查询资料及已完成的协议消息；人工继续或再次发送需求创建新对话轮次，不复活失败轮次，也不保证新轮次完全避免重复查询。服务重启时收束中断的执行记录，沿用本项目中断轮次的资料恢复方式，不自动重放模型请求、工具调用或等待中的重试。此边界与查询资料核实失败后的降级策略、后台旅行者偏好提取的独立恢复策略并存，遵循 [ADR 0001](0001-domestic-planning-with-booking-entry.md)、[ADR 0002](0002-cross-session-traveler-preferences.md) 和 [ADR 0003](0003-relative-travel-dates.md)。

参考 deepseek-harness 提交 `d743267388641bc76f17c45ce8b4c231aed1d32c` 的[默认策略](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/llm/llm/src/retry-policy.ts#L14-L24)、[重试决策与等待](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/llm/llm-retry/src/index.ts#L149-L240)、[失败终态](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/core/agent-loop/src/agent.ts#L344-L387)及[重启恢复](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/core/agent-loop/src/index.ts#L874-L918)。采用其默认恢复行为，按现有 Anthropic 兼容客户端和停止契约适配；不引入插件框架、无限重试模式或流式传输。实施规格见 [Issue #53](https://github.com/lll1024/Potato/issues/53)，实现与验证记录见[模型重试验收](../acceptance/model-retries.md)。
