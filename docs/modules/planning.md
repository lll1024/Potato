# 旅行规划：当前公开契约

公开入口为 [travel_app.planning](../../travel_app/planning/__init__.py)。主要操作是异步 `plan(text, client, tools, model, *, seed=None, continuation=None, observer=None, stop_requested=None, stop_reason="user_stop", preference_background="", clock=None, history_records=None)`。Web 与真实 CLI 都调用它；不创建数据库或后台偏好任务。

`client` 是已配置的 Anthropic 异步客户端；`tools` 是已发现能力的现有地图或组合查询 adapter。生命周期由装配方管理。模型、地图、网页的生产连接配置及超时保持既有启动行为；调用方可以注入可控外部响应。查询 adapter 的独立公开契约留给 #83，本入口不意味着该迁移已经完成。

`seed` 是本机记录在同一快照中读取的 `PlanningSeed`；首次对话可省略。CLI 将上次返回的 `continuation` 原样传入，不创建或拆解它；它仅在当前进程内有效，不是持久会话身份。两者互斥，同时传入抛出 `ValueError`。输入、完整协议、模型视图和已确定旅行日期由规划内部协调；调用方不追加消息或自行同步视图。偏好背景作为只读文本交入，历史原文通过同步 `history_records()` 回调按需读取。

返回不可变 [PlanningResult](../../travel_app/agent.py)，包含：

| 字段 | 含义 |
| --- | --- |
| `answer` | 已脱敏回答，可能是正常回答或未完成说明 |
| `status`、`reason` | 原协议的 `completed` / `terminated` / `failed` 及原因；完成时原因为 `None` |
| `answer_source` | `model` 或 `application` |
| `tool_error_count` | 实际失败的工具数；未启动调用不作为真实失败计数 |
| `material` | 不可变 `TurnMaterial`，包含完整协议和有效上下文保存材料 |
| `continuation` | CLI 使用的不透明进程内延续凭证 |

`fields()` 提供与记录终态字段相同的字典。结果直接来自执行循环的结构化返回，不由观察事件推导。`observer(kind, data)` 只承担事实记录与通知；即使没有 observer，返回值仍完整。`turn.finished` 观察不表示数据库已保存；只有本机记录的 `finish` 成功返回后，会话历史才算完成。

完成、用户停止、服务退出、预算耗尽、模型失败保留原状态和原因。`stop_requested` 在调用边界生效，等待已启动调用的真实返回，不取消或伪造其结果。事实记录回调必须在保存失败时抛出 `RecordAbort`，并通知会话停止接受输入；该异常贯穿规划，不转换成 `model_error`。恢复记录种子失败抛出 `RecordAbort(kind="turn.context")`；生成保存材料失败抛出 `RecordAbort(kind="turn.finished")`，均不返回可保存结果。其他未预期执行异常保留 `execution_error` 及原有未完成说明。

整轮工具 128 次、历史回读最多 8 次、实际模型请求 64 次及无工具收尾预留保持；地图串行，网页最多十个在途，重试、摘要和恢复仍共享请求预算。默认配置、旅行规则和模型可见输入未因入口变化而扩大。

`validate_configuration() -> None` 供启动装配检查模型上下文容量配置；错误为公开 `ContextConfigurationError`，可直接展示。CLI 的日期状态保存在不透明延续凭证中，观察回调只展示进度。

当前实现仍在 [agent.py](../../travel_app/agent.py)，其中 `_execute_plan` 与资料 adapter 是 #83 的明确迁移实现；完整协议、模型视图、摘要、容量和回读已通过 [模型上下文](context.md)公开入口管理；旧 `agent_loop` 仅供现有内部测试适配，生产入口不再调用它。公开入口直接导出实际规划操作。此阶段资料获取尚待 #83 封装。

验证：[规划契约测试](../../tests/test_agent.py)、[记录故障](../../tests/test_storage.py)、[调用预算](../../tests/test_call_budget.py)、[固定 Web/SSE/CLI 与旧库基线](../../tests/test_migration_baseline.py)。
