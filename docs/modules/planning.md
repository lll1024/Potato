# 旅行规划：当前公开契约

公开入口为 [travel_app.planning](../../travel_app/planning/__init__.py)。主要操作是异步 `plan(text, client, tools, model, *, seed=None, continuation=None, observer=None, stop_requested=None, stop_reason="user_stop", preference_background="", clock=None, history_records=None, max_rounds=80, max_tool_calls=128, context_policy=None)`。Web 与真实 CLI 都调用它；不创建数据库或后台偏好任务。

`client` 是已配置的 Anthropic 异步客户端；`tools` 是已发现能力的 `PlanningTools`（实际为资料获取公开的 `Retrieval` 类型）。生命周期由装配方管理。模型、地图、网页的生产连接配置及超时保持既有启动行为；调用方可以注入可控外部响应。查询能力说明、分段执行与失败语义见[资料获取契约](retrieval.md)。

`seed` 是本机记录在同一快照中读取的 `PlanningSeed`；首次对话可省略。CLI 将上次返回的 `continuation` 原样传入，不创建或拆解它；它仅在当前进程内有效，不是持久会话身份。两者互斥，同时传入抛出 `ValueError`。输入、完整协议、模型视图和已确定旅行日期由规划内部协调；调用方不追加消息或自行同步视图。偏好背景作为只读文本交入，历史原文通过同步 `history_records()` 回调按需读取。

返回不可变 [PlanningResult](../../travel_app/planning/runtime.py)，包含：

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

`configured_planning(*, retrieval_factory=None)` 检查配置并管理生产模型与查询连接，返回 `PlanningResources(client, tools, model)`；Web 将它装入会话运行资源，CLI 复用同一模型装配。可注入查询资源工厂重定向外部响应，默认连接与超时不变。`run_cli(..., retrieval_factory=None, model_client_factory=None)`、`main(*, load_environment=None, cli_runner=None)` 提供根入口的显式装配 seam；模型客户端工厂只替换连接资源，实际调用、重试、摘要和预算仍走生产 implementation；它们不替换规划、查询、上下文或保存 implementation。

生产装配从项目根 `.env` 加载环境（覆盖已有同名环境），默认静态资源与工作目录解析见[会话](sessions.md)。需要 `AMAP_MAPS_API_KEY`、`MODEL_ID` 及模型认证；`ANTHROPIC_BASE_URL` 可指定兼容服务，提供时使用 `ANTHROPIC_API_KEY`，否则也可用 `ANTHROPIC_AUTH_TOKEN`。模型请求超时 60 秒，SDK 自动重试关闭，由规划执行有界重试；配置错误由启动入口诊断。`configured_planning` 本身消费当前环境，不额外读取 `.env`。

`run_cli(api_key, *, check_amap=False, clock=None, retrieval_factory=None, model_client_factory=None)` 在上下文管理器内打开查询与模型资源；`retrieval_factory(api_key, *, include_sources=True, secrets=())` 产出已发现的 `Retrieval`。`model_client_factory` 接受 Anthropic 客户端同形构造参数并返回异步上下文管理器。`check_amap=True` 仅调用地图检查，跳过模型、网页及偏好后台；普通 CLI 输入 `q` 退出，同一进程的续聊自动使用延续凭证。

`configured_planning(...)` 返回异步上下文管理器，必须用 `async with`；其产出不可变 `PlanningResources(client: AsyncAnthropic, tools: Retrieval, model: str)`。`retrieval_factory` 同样返回异步上下文管理器。最小生产调用（环境已配置）为：

```python
from travel_app.planning import configured_planning, plan

async def answer(text):
    async with configured_planning() as ready:
        result = await plan(text, ready.client, ready.tools, ready.model)
        return result.answer, result.status, result.reason
```

`PlanningResult` 是不可变记录值；`answer: str`、`status: str`、`reason: str | None`、`answer_source: str`、`tool_error_count: int`、`material: TurnMaterial`、`continuation: Continuation`。调用方消费返回值，不自行构造结果或拆开延续凭证。`stop_reason` 可为字符串或无参同步回调，默认 `user_stop`；会话退出使用 `service_shutdown`，记录故障设置 `storage_failure` 并以 `RecordAbort` 中止。

| 结果类别 | `status` 与 `reason` |
| --- | --- |
| 正常最终回答 | `completed` / `None` |
| 输出截断 | `terminated` / `output_limit` |
| 停止或预算 | `terminated` / `user_stop`、`service_shutdown`、`budget`，或装配提供的停止原因 |
| 上下文启动或安全容量阻止 | `terminated` / `context_configuration`、`context_budget` |
| 普通模型失败或未预期执行失败 | `failed` / `model_error`、`execution_error` |
| 明确超限恢复失败 | `failed` / `context_window_exceeded`、`context_recovery_unavailable` |

模型事实的 `error.code` 与轮次 `reason` 各有用途：网络、超时、429、5xx、空响应分别为 `TRANSPORT`、`TIMEOUT`、`RATE_LIMIT`、`SERVER`、`EMPTY_RESPONSE`，普通步骤最多追加 5 次尝试，仍受整轮预算约束；401/403 为 `AUTH`、额度不足为 `QUOTA`、非法请求为 `INVALID_REQUEST`，不普通重试。明确超限为 `CONTEXT_WINDOW_EXCEEDED`，走一次上下文恢复。其他 HTTP 状态为 `HTTP_<状态码>`，非模型调用阶段或未知错误为 `UNKNOWN`。普通退避从 0.5 秒指数增长、±10% 抖动且最多 10 秒；服务给出有效正数 `Retry-After` 时优先使用，超过 10 秒不继续等待。最终收尾没有普通重试。上层通常只需消费结构化轮次结果，界面诊断可另展示真实请求事实。

`max_rounds`、`max_tool_calls` 可收紧原有运行限额，分别受 80 和 128 硬上限限制；`context_policy` 可注入公开 `ContextPolicy`，省略时仍读取原环境配置。既有受限运行测试通过此入口执行，不再需要生产 `agent_loop` 适配。资料获取实例的可用状态保留到连接生命周期结束，整轮工具和实际模型预算在每次 `plan` 重建。

模型步骤在 [runtime.py](../../travel_app/planning/runtime.py)；[batch.py](../../travel_app/planning/batch.py)统一登记调用身份、按提出顺序预留工具额度、切分连续外部段、等待历史屏障并按原序收齐一次完整结果；模型步骤不持有分段拼接或供应商状态。历史回读将前段真实结果与后段占位一并交给上下文，按完整请求余量分页。外部段未启动沿用原预留计数，历史局部上限拒绝不消耗总账；实际失败照常计数。

[budget.py](../../travel_app/planning/budget.py)拥有工具总账与实际模型请求序号，摘要和恢复通过同一额度回调扣账，普通请求仅使用前 63 次，第 64 次预留给关闭工具的最终回答。达到预算时先等待在途返回；最终请求不再摘要、查询或重试。逻辑步骤仍独立计数。共同模型事实采集在无业务运行状态的 [model_execution.py](../../travel_app/model_execution.py)，普通重试与摘要的局部策略继续由各自能力拥有。

完整协议、模型视图、摘要、容量和回读通过[模型上下文](context.md)管理；日期解释在 [dates.py](../../travel_app/planning/dates.py)。旧 `travel_app/agent.py`、查询 adapter 和日期文件已迁入所属能力，生产和测试调用均更新到当前入口。阶段证据见 [#83 验收](../acceptance/retrieval-planning.md)。

验证：[规划契约测试](../../tests/test_agent.py)、[记录故障](../../tests/test_storage.py)、[调用预算](../../tests/test_call_budget.py)、[固定 Web/SSE/CLI 与旧库基线](../../tests/test_migration_baseline.py)。
